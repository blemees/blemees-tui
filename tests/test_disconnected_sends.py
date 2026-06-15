"""No user action may crash the app while the daemon is unreachable (#15)."""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")

from blemees_tui.app import BlemeesTuiApp  # noqa: E402
from blemees_tui.connection import ConnectionError_  # noqa: E402
from blemees_tui.state import SessionMode, SessionState  # noqa: E402
from blemees_tui.widgets.composer import ComposerWidget  # noqa: E402


async def _start_app_no_socket(monkeypatch):
    async def _noop(self):
        return

    monkeypatch.setattr("blemees_tui.connection.Connection.start", _noop)
    monkeypatch.setattr("blemees_tui.connection.Connection.stop", _noop)


def _raise_disconnected(monkeypatch, *verbs):
    async def _boom(self, *a, **kw):
        raise ConnectionError_("not connected")

    for verb in verbs:
        monkeypatch.setattr(f"blemees_tui.connection.Connection.{verb}", _boom)


def _seed(app, sid="s1"):
    sess = SessionState(session_id=sid, mode=SessionMode.OWNED)
    app.state.sessions[sid] = sess
    app.state.active_session_id = sid
    return sess


@pytest.mark.asyncio
async def test_submit_while_disconnected_queues_instead_of_crashing(
    isolated_state_dir, monkeypatch
):
    await _start_app_no_socket(monkeypatch)
    _raise_disconnected(monkeypatch, "send_user")
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        sess = _seed(app)
        app.state.connection_status = "disconnected"
        await app.on_composer_widget_submit(ComposerWidget.Submit("hello there"))
        await pilot.pause()
        assert sess.pending_sends == ["hello there"]
        assert app.return_code is None  # app alive — used to exit 1 (#15)


@pytest.mark.asyncio
async def test_send_failure_requeues_at_front(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    _raise_disconnected(monkeypatch, "send_user")
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        sess = _seed(app)
        # Looks connected, but the socket dies mid-send: must re-queue, not crash.
        app.state.connection_status = "connected"
        sess.pending_sends.append("second")
        await app._send_user_message("s1", "first")
        await pilot.pause()
        assert sess.pending_sends == ["first", "second"]  # order preserved
        log = [e.category for e in app.state.event_log]
        assert "send_queued" in log


@pytest.mark.asyncio
async def test_interrupt_and_close_while_disconnected_do_not_crash(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    _raise_disconnected(monkeypatch, "interrupt", "close_session")
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        _seed(app)
        await app._interrupt_session("s1")
        await pilot.pause()
        assert app.return_code is None
        await app._close_session_by_id("s1", delete=False)
        await pilot.pause()
        # Local cleanup proceeded; app alive.
        assert "s1" not in app.state.sessions
        assert app.return_code is None
        cats = [e.category for e in app.state.event_log]
        assert "interrupt_failed" in cats and "close_offline" in cats


@pytest.mark.asyncio
async def test_broadcast_while_disconnected_queues_for_all_marked(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    _raise_disconnected(monkeypatch, "send_user")
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        a = _seed(app, "a")
        b = _seed(app, "b")
        a.marked = b.marked = True
        app.state.connection_status = "disconnected"
        await app._broadcast("fan out")
        await pilot.pause()
        assert a.pending_sends == ["fan out"] and b.pending_sends == ["fan out"]
        assert app.return_code is None


@pytest.mark.asyncio
async def test_queued_messages_flush_on_reconnect(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    sent: list[tuple[str, str]] = []

    async def fake_send(self, sid, text):
        sent.append((sid, text))

    monkeypatch.setattr("blemees_tui.connection.Connection.send_user", fake_send)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        sess = _seed(app)
        sess.pending_sends.append("queued while offline")
        app.state.connection_status = "disconnected"
        from blemees_tui.connection import ConnectionStatus

        app._on_connection_status(ConnectionStatus(state="connected"))
        await pilot.pause()
        await pilot.pause()
        assert sent == [("s1", "queued while offline")]
        assert sess.pending_sends == []


@pytest.mark.asyncio
async def test_duplicate_queued_texts_are_not_dropped_on_requeue(isolated_state_dir, monkeypatch):
    # pending_sends may legally hold the same text twice; a failed send must
    # re-insert even when a duplicate is queued (review feedback on #36).
    await _start_app_no_socket(monkeypatch)
    _raise_disconnected(monkeypatch, "send_user")
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        sess = _seed(app)
        app.state.connection_status = "connected"
        sess.pending_sends.append("same")
        await app._send_user_message("s1", "same")
        await pilot.pause()
        assert sess.pending_sends == ["same", "same"]

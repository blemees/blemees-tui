"""Post-hello reconcile of snapshot-restored sessions against the daemon (#30)."""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")

from blemees_tui.app import BlemeesTuiApp  # noqa: E402
from blemees_tui.snapshot import load_snapshot, save_snapshot  # noqa: E402
from blemees_tui.state import SessionState  # noqa: E402


async def _start_app_no_socket(monkeypatch):
    async def _noop(self):
        return

    monkeypatch.setattr("blemees_tui.connection.Connection.start", _noop)
    monkeypatch.setattr("blemees_tui.connection.Connection.stop", _noop)


def _hello_ack():
    return {
        "type": "hello_ack",
        "daemon": "blemees-agentd/0.11.0",
        "protocol": "blemees/3",
        "pid": 1,
        "agents": {"claude-agent-acp": "available"},
        "profiles": ["default"],
    }


@pytest.mark.asyncio
async def test_stale_snapshot_sessions_dropped_on_hello(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)

    async def fake_list(self, *, cwd=None):
        return [{"session_id": "alive", "cwd": "/p"}]

    monkeypatch.setattr("blemees_tui.connection.Connection.list_sessions", fake_list)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        # Two sessions "restored from snapshot"; the daemon only knows one.
        # Real on-disk snapshots so the deletion side effect is observable.
        for sid in ("alive", "stale"):
            sess = SessionState(session_id=sid)
            app.state.sessions[sid] = sess
            save_snapshot(sess)
        app.state.active_session_id = "stale"
        assert load_snapshot("stale") is not None
        app._handle_frame(_hello_ack())
        await pilot.pause()
        await pilot.pause()
        assert "alive" in app.state.sessions
        assert "stale" not in app.state.sessions
        # The stale snapshot is deleted from disk; the live one remains.
        assert load_snapshot("stale") is None
        assert load_snapshot("alive") is not None
        # Active selection moved off the dropped session.
        assert app.state.active_session_id != "stale"
        log = [(e.category, e.message) for e in app.state.event_log]
        assert any(cat == "session_reconciled" and "stale" in msg for cat, msg in log)


@pytest.mark.asyncio
async def test_session_opened_during_reconcile_survives(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    app = BlemeesTuiApp()

    async def fake_list(self, *, cwd=None):
        # A session opened while the list request is in flight: present in
        # local state but absent from the (already-built) daemon reply.
        app.state.sessions["opened-mid-flight"] = SessionState(session_id="opened-mid-flight")
        return []

    monkeypatch.setattr("blemees_tui.connection.Connection.list_sessions", fake_list)
    async with app.run_test() as pilot:
        app._handle_frame(_hello_ack())
        await pilot.pause()
        await pilot.pause()
        assert "opened-mid-flight" in app.state.sessions


@pytest.mark.asyncio
async def test_reconcile_failure_is_nonfatal(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)

    async def fake_list(self, *, cwd=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("blemees_tui.connection.Connection.list_sessions", fake_list)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        app.state.sessions["kept"] = SessionState(session_id="kept")
        app._handle_frame(_hello_ack())
        await pilot.pause()
        await pilot.pause()
        # Nothing dropped, app alive, failure logged.
        assert "kept" in app.state.sessions
        assert any(e.category == "reconcile_failed" for e in app.state.event_log)


@pytest.mark.asyncio
async def test_malformed_list_rows_do_not_crash_reconcile(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)

    async def fake_list(self, *, cwd=None):
        # Daemon misbehaving: non-dict rows and rows without session_id mixed
        # in with a valid one — reconcile must survive and still work.
        return [None, "junk", 42, {"no_session_id": True}, {"session_id": "alive"}]

    monkeypatch.setattr("blemees_tui.connection.Connection.list_sessions", fake_list)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        app.state.sessions["alive"] = SessionState(session_id="alive")
        app.state.sessions["stale"] = SessionState(session_id="stale")
        app._handle_frame(_hello_ack())
        await pilot.pause()
        await pilot.pause()
        assert "alive" in app.state.sessions
        assert "stale" not in app.state.sessions
        # No crash logged by the done-callback.
        assert not any(e.category == "reconcile_crashed" for e in app.state.event_log)

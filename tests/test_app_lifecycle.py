"""App-level session lifecycle: take-ownership and close-on-frame paths."""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")

from blemees_tui.app import BlemeesTuiApp  # noqa: E402
from blemees_tui.state import SessionState  # noqa: E402


async def _start_app_no_socket(monkeypatch):
    async def _noop_start(self):
        return

    async def _noop_stop(self):
        return

    monkeypatch.setattr("blemees_tui.connection.Connection.start", _noop_start)
    monkeypatch.setattr("blemees_tui.connection.Connection.stop", _noop_stop)


@pytest.mark.asyncio
async def test_take_ownership_flips_mode_and_tracks(isolated_state_dir, monkeypatch):
    """[Take ownership] should call Connection.open_session(resume=True) and
    flip mode WATCHING→OWNED on success."""
    await _start_app_no_socket(monkeypatch)

    open_calls: list[dict] = []

    async def fake_open(
        self, session_id, *, backend, options=None, resume=False, last_seen_seq=None
    ):
        open_calls.append(
            {
                "session_id": session_id,
                "backend": backend,
                "resume": resume,
                "last_seen_seq": last_seen_seq,
            }
        )
        return {"type": "agent.opened", "session_id": session_id, "last_seq": 0}

    monkeypatch.setattr("blemees_tui.connection.Connection.open_session", fake_open)

    app = BlemeesTuiApp()
    async with app.run_test():
        sess = SessionState(session_id="sid_w", backend="claude", last_seen_seq=42)
        from blemees_tui.state import SessionMode

        sess.mode = SessionMode.WATCHING
        app.state.sessions["sid_w"] = sess
        app.state.active_session_id = "sid_w"

        await app._take_ownership("sid_w")

        assert sess.mode == SessionMode.OWNED
        assert open_calls == [
            {"session_id": "sid_w", "backend": "claude", "resume": True, "last_seen_seq": 42}
        ]
        assert "sid_w" in app._connection._tracked
        assert app._connection._tracked["sid_w"]["kind"] == "owned"


@pytest.mark.asyncio
async def test_session_closed_frame_drops_session(isolated_state_dir, monkeypatch):
    """A ``agent.session_closed`` from the daemon (watcher-side, §16.2)
    should drop the session from state and clear the active selection."""
    await _start_app_no_socket(monkeypatch)

    app = BlemeesTuiApp()
    async with app.run_test():
        # Inject a session by hand.
        sess = SessionState(session_id="sid1", backend="claude", title="t1")
        app.state.sessions["sid1"] = sess
        app.state.active_session_id = "sid1"

        app._handle_frame(
            {
                "type": "agent.session_closed",
                "session_id": "sid1",
                "reason": "owner_closed",
            }
        )

        assert "sid1" not in app.state.sessions
        assert app.state.active_session_id is None

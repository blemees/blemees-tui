"""Session-scope errors land in the event log and stay there (#48)."""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")

from blemees_tui.app import BlemeesTuiApp  # noqa: E402
from blemees_tui.reducer import apply_user_prompt  # noqa: E402
from blemees_tui.state import EventLogSource, SessionState  # noqa: E402


@pytest.fixture(autouse=True)
def _no_socket(monkeypatch):
    async def _noop(self):
        return

    monkeypatch.setattr("blemees_tui.connection.Connection.start", _noop)
    monkeypatch.setattr("blemees_tui.connection.Connection.stop", _noop)


def _logged(app, session_id):
    return [
        (e.source, e.category, e.message) for e in app.state.event_log if e.session_id == session_id
    ]


@pytest.mark.asyncio
async def test_session_error_stays_in_event_log_after_bubble_clears(isolated_state_dir):
    """Regression (#48): session errors were kept only in ``pending_errors``,
    so an ``end_turn`` result wiped them from the UI for good."""
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sess = SessionState(session_id="s1")
        app.state.sessions["s1"] = sess
        app.state.active_session_id = "s1"
        app._handle_frame(
            {
                "type": "session.error",
                "session_id": "s1",
                "code": "auth_required",
                "message": "session expired",
            }
        )
        assert len(sess.pending_errors) == 1
        # A later turn ends cleanly — the bubble clears (#19) ...
        apply_user_prompt(sess, "retry")
        app._handle_frame(
            {"type": "session.result", "session_id": "s1", "seq": 3, "stop_reason": "end_turn"}
        )
        await pilot.pause()
        assert sess.pending_errors == []
        # ... but the error is still in the event log.
        assert _logged(app, "s1") == [
            (EventLogSource.DAEMON_ERROR, "auth_required", "session expired")
        ]


@pytest.mark.asyncio
async def test_session_scoped_error_frame_is_logged(isolated_state_dir):
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.state.sessions["s1"] = SessionState(session_id="s1")
        app._handle_frame(
            {
                "type": "error",
                "session_id": "s1",
                "code": "session_busy",
                "message": "turn in progress",
            }
        )
        await pilot.pause()
        assert _logged(app, "s1") == [
            (EventLogSource.DAEMON_ERROR, "session_busy", "turn in progress")
        ]


@pytest.mark.asyncio
async def test_session_unknown_is_logged_before_session_is_dropped(isolated_state_dir):
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.state.sessions["gone"] = SessionState(session_id="gone")
        app._handle_frame(
            {
                "type": "error",
                "session_id": "gone",
                "code": "session_unknown",
                "message": "no such session",
            }
        )
        await pilot.pause()
        assert "gone" not in app.state.sessions
        assert _logged(app, "gone") == [
            (EventLogSource.DAEMON_ERROR, "session_unknown", "no such session")
        ]

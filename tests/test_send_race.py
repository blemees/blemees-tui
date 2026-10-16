"""A reply that lands before the send returns must not strand the turn (#13).

The e2e smoke caught it: with a fast agent, ``session.update`` +
``session.result`` were reduced while ``send_user`` was still awaiting
``drain()``. The prompt was then recorded *after* the result, opening a
second turn whose ``turn_active`` nothing ever cleared.
"""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")

from blemees_tui.app import BlemeesTuiApp  # noqa: E402
from blemees_tui.connection import ConnectionError_  # noqa: E402
from blemees_tui.state import SessionMode, SessionState, TextBlock  # noqa: E402


@pytest.fixture(autouse=True)
def _no_socket(monkeypatch):
    async def _noop(self):
        return

    monkeypatch.setattr("blemees_tui.connection.Connection.start", _noop)
    monkeypatch.setattr("blemees_tui.connection.Connection.stop", _noop)


def _seed(app, sid="s1"):
    sess = SessionState(session_id=sid, mode=SessionMode.OWNED)
    app.state.sessions[sid] = sess
    app.state.active_session_id = sid
    app.state.connection_status = "connected"
    return sess


@pytest.mark.asyncio
async def test_reply_during_send_closes_the_prompted_turn(isolated_state_dir, monkeypatch):
    app = BlemeesTuiApp()

    async def fast_agent(self, session_id, text):
        # The whole reply arrives while the write is still draining.
        app._handle_frame(
            {
                "type": "session.update",
                "session_id": session_id,
                "seq": 1,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "pong"},
                },
            }
        )
        app._handle_frame(
            {
                "type": "session.result",
                "session_id": session_id,
                "seq": 2,
                "stop_reason": "end_turn",
            }
        )

    monkeypatch.setattr("blemees_tui.connection.Connection.send_user", fast_agent)
    async with app.run_test() as pilot:
        sess = _seed(app)
        await app._send_user_message("s1", "say pong")
        await pilot.pause()
        assert not sess.turn_active
        assert len(sess.turns) == 1
        assert sess.turns[0].user_text == "say pong"
        assert [b.text for b in sess.turns[0].blocks if isinstance(b, TextBlock)] == ["pong"]


@pytest.mark.asyncio
async def test_failed_send_leaves_no_phantom_turn(isolated_state_dir, monkeypatch):
    async def _boom(self, *a, **kw):
        raise ConnectionError_("not connected")

    monkeypatch.setattr("blemees_tui.connection.Connection.send_user", _boom)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        sess = _seed(app)
        await app._send_user_message("s1", "hello")
        await pilot.pause()
        assert sess.turns == []
        assert not sess.turn_active
        assert sess.title == ""
        assert sess.pending_sends == ["hello"]

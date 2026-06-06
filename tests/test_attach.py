"""Owner/viewer attach flow (#3)."""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")

from blemees_tui.app import BlemeesTuiApp  # noqa: E402
from blemees_tui.state import SessionMode  # noqa: E402
from blemees_tui.widgets.modals import AttachModal  # noqa: E402


async def _start_app_no_socket(monkeypatch):
    async def _noop(self):
        return

    monkeypatch.setattr("blemees_tui.connection.Connection.start", _noop)
    monkeypatch.setattr("blemees_tui.connection.Connection.stop", _noop)


def _capture_attach(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def fake_attach(self, session_id, *, as_role="viewer", last_seen_seq=0):
        calls.append({"session_id": session_id, "as_role": as_role, "last_seen_seq": last_seen_seq})
        return {"type": "session.attached", "session_id": session_id, "last_seq": 0}

    monkeypatch.setattr("blemees_tui.connection.Connection.attach_session", fake_attach)
    return calls


@pytest.mark.asyncio
async def test_attach_as_viewer_sets_watching(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    calls = _capture_attach(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test():
        await app.on_attach_modal_submit(AttachModal.Submit("sid_v", as_role="viewer"))
        assert app.state.sessions["sid_v"].mode == SessionMode.WATCHING
        assert calls == [{"session_id": "sid_v", "as_role": "viewer", "last_seen_seq": 0}]
        assert app._connection._tracked["sid_v"]["kind"] == "watching"


@pytest.mark.asyncio
async def test_attach_as_owner_takes_over(isolated_state_dir, monkeypatch):
    await _start_app_no_socket(monkeypatch)
    calls = _capture_attach(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test():
        await app.on_attach_modal_submit(AttachModal.Submit("sid_o", as_role="owner"))
        assert app.state.sessions["sid_o"].mode == SessionMode.OWNED
        assert calls[0]["as_role"] == "owner"
        assert app._connection._tracked["sid_o"]["kind"] == "owned"


@pytest.mark.asyncio
async def test_permission_response_answers_and_clears(isolated_state_dir, monkeypatch):
    """Answering an inline permission card posts the choice and clears it (#4)."""
    from blemees_tui.state import SessionState
    from blemees_tui.widgets import ChatPaneWidget

    await _start_app_no_socket(monkeypatch)
    sent: list[dict] = []

    async def fake_respond(self, session_id, request_id, *, outcome, option_id=None):
        sent.append(
            {
                "session_id": session_id,
                "request_id": request_id,
                "outcome": outcome,
                "option_id": option_id,
            }
        )

    monkeypatch.setattr("blemees_tui.connection.Connection.respond_permission", fake_respond)
    app = BlemeesTuiApp()
    async with app.run_test():
        sess = SessionState(session_id="sid_p")
        sess.pending_permission = {"request_id": "perm_1", "options": [], "tool_call": {}}
        app.state.sessions["sid_p"] = sess
        await app.on_chat_pane_widget_permission_response(
            ChatPaneWidget.PermissionResponse(
                "sid_p", "perm_1", outcome="selected", option_id="allow"
            )
        )
        assert sent == [
            {
                "session_id": "sid_p",
                "request_id": "perm_1",
                "outcome": "selected",
                "option_id": "allow",
            }
        ]
        assert sess.pending_permission is None

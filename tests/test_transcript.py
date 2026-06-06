"""Markdown transcript export (M4.3)."""

from __future__ import annotations

from blemees_tui.reducer import apply, apply_user_prompt
from blemees_tui.state import SessionState
from blemees_tui.transcript import render


def test_render_includes_user_assistant_tool_blocks_and_usage():
    sess = SessionState(
        session_id="5a01abcd-1234-5678-9abc-def012345678", backend="claude", title="t"
    )
    apply_user_prompt(sess, "do it")
    sid = sess.session_id

    def upd(seq, update):
        apply(sess, {"type": "session.update", "session_id": sid, "seq": seq, "update": update})

    upd(2, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "okay"}})
    upd(
        3,
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "tu1",
            "title": "Read",
            "kind": "read",
            "rawInput": {"path": "/tmp/x"},
        },
    )
    upd(
        4,
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "tu1",
            "status": "completed",
            "content": [{"type": "content", "content": {"type": "text", "text": "contents"}}],
        },
    )
    apply(
        sess,
        {
            "type": "session.result",
            "session_id": sid,
            "seq": 5,
            "stop_reason": "end_turn",
            "usage": {"inputTokens": 5, "outputTokens": 10},
        },
    )

    md = render(sess)
    assert md.startswith("# t\n")
    assert "**user**" in md
    assert "**assistant**" in md
    assert "**tool_use** · `Read`" in md
    assert "tool_result" in md
    assert "in=5" in md
    assert "out=10" in md


def test_render_handles_empty_session():
    sess = SessionState(session_id="abc", backend="claude")
    md = render(sess)
    assert "# abc" in md
    # No turns → no "## Turn" headers.
    assert "## Turn" not in md

"""Markdown transcript export (M4.3)."""

from __future__ import annotations

from blemees_tui.reducer import apply
from blemees_tui.state import SessionState
from blemees_tui.transcript import render


def test_render_includes_user_assistant_tool_blocks_and_usage():
    sess = SessionState(session_id="5a01abcd-1234-5678-9abc-def012345678", backend="claude", title="t")
    apply(sess, {"type": "agent.user", "session_id": sess.session_id, "seq": 1, "message": {"role": "user", "content": "do it"}})
    apply(sess, {"type": "agent.delta", "session_id": sess.session_id, "seq": 2, "kind": "text", "text": "ok"})
    apply(
        sess,
        {
            "type": "agent.message",
            "session_id": sess.session_id,
            "seq": 3,
            "role": "assistant",
            "content": [{"type": "text", "text": "okay"}],
        },
    )
    apply(
        sess,
        {
            "type": "agent.tool_use",
            "session_id": sess.session_id,
            "seq": 4,
            "tool_use_id": "tu1",
            "name": "Read",
            "input": {"path": "/tmp/x"},
        },
    )
    apply(
        sess,
        {
            "type": "agent.tool_result",
            "session_id": sess.session_id,
            "seq": 5,
            "tool_use_id": "tu1",
            "output": "contents",
        },
    )
    apply(
        sess,
        {
            "type": "agent.result",
            "session_id": sess.session_id,
            "seq": 6,
            "subtype": "success",
            "duration_ms": 1500,
            "usage": {"input_tokens": 5, "output_tokens": 10},
        },
    )

    md = render(sess)
    assert md.startswith("# t\n")
    assert "**user**" in md
    assert "**assistant**" in md
    assert "**tool_use** · `Read`" in md
    assert "tool_result" in md
    assert "duration=1.50s" in md
    assert "in=5" in md
    assert "out=10" in md


def test_render_handles_empty_session():
    sess = SessionState(session_id="abc", backend="claude")
    md = render(sess)
    assert "# abc" in md
    # No turns → no "## Turn" headers.
    assert "## Turn" not in md

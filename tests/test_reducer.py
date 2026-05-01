"""Reducer smoke tests (spec §17.1).

The reducer is the single source of truth for transcript state, so these
tests exercise the high-traffic paths: streaming text deltas, tool
use/result pairing, ``agent.result`` lock + usage merge, watcher signals.
"""

from __future__ import annotations

from blemees_tui.reducer import apply
from blemees_tui.state import (
    SessionMode,
    SessionState,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)


def _new() -> SessionState:
    return SessionState(session_id="s1", backend="claude")


def test_user_then_text_delta_then_message_then_result():
    s = _new()
    apply(s, {"type": "agent.user", "session_id": "s1", "seq": 1, "message": {"role": "user", "content": "hi"}})
    apply(s, {"type": "agent.delta", "session_id": "s1", "seq": 2, "kind": "text", "text": "hel"})
    apply(s, {"type": "agent.delta", "session_id": "s1", "seq": 3, "kind": "text", "text": "lo"})
    apply(
        s,
        {
            "type": "agent.message",
            "session_id": "s1",
            "seq": 4,
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
        },
    )
    apply(
        s,
        {
            "type": "agent.result",
            "session_id": "s1",
            "seq": 5,
            "subtype": "success",
            "duration_ms": 1234,
            "usage": {"input_tokens": 10, "output_tokens": 20},
        },
    )

    assert len(s.turns) == 1
    turn = s.turns[0]
    assert turn.user_text == "hi"
    text_blocks = [b for b in turn.blocks if isinstance(b, TextBlock)]
    assert text_blocks and text_blocks[-1].text == "hello"
    assert text_blocks[-1].finalized
    assert turn.locked
    assert turn.duration_ms == 1234
    assert turn.usage.input_tokens == 10
    assert s.cumulative_usage.output_tokens == 20
    assert s.last_seq == 5
    assert not s.turn_active


def test_thinking_delta_creates_separate_block():
    s = _new()
    apply(s, {"type": "agent.user", "session_id": "s1", "seq": 1, "message": {"role": "user", "content": "?"}})
    apply(s, {"type": "agent.delta", "session_id": "s1", "seq": 2, "kind": "thinking", "text": "ponder"})
    apply(s, {"type": "agent.delta", "session_id": "s1", "seq": 3, "kind": "text", "text": "answer"})

    blocks = s.turns[-1].blocks
    assert any(isinstance(b, ThinkingBlock) and b.text == "ponder" for b in blocks)
    assert any(isinstance(b, TextBlock) and b.text == "answer" for b in blocks)


def test_tool_use_then_tool_result_pairing():
    s = _new()
    apply(s, {"type": "agent.user", "session_id": "s1", "seq": 1, "message": {"role": "user", "content": "do"}})
    apply(
        s,
        {
            "type": "agent.tool_use",
            "session_id": "s1",
            "seq": 2,
            "tool_use_id": "tu1",
            "name": "Read",
            "input": {"path": "/x"},
        },
    )
    apply(
        s,
        {
            "type": "agent.tool_result",
            "session_id": "s1",
            "seq": 3,
            "tool_use_id": "tu1",
            "output": "contents",
            "is_error": False,
        },
    )
    block = next(b for b in s.turns[-1].blocks if isinstance(b, ToolUseBlock))
    assert block.name == "Read"
    assert block.result_text == "contents"
    assert not block.is_error


def test_session_taken_flips_to_detached():
    s = _new()
    apply(s, {"type": "blemeesd.session_taken", "session_id": "s1", "by_peer_pid": 99})
    assert s.mode == SessionMode.DETACHED
    assert s.taken_by_pid == 99


def test_replay_gap_marks_session():
    s = _new()
    apply(s, {"type": "blemeesd.replay_gap", "session_id": "s1"})
    assert s.replay_gap is True


def test_system_init_seeds_started_at_ms():
    s = _new()
    apply(
        s,
        {
            "type": "agent.system_init",
            "session_id": "s1",
            "seq": 1,
            "model": "claude-sonnet-4-6",
            "cwd": "/proj",
            "context_window": 200000,
        },
    )
    assert s.model == "claude-sonnet-4-6"
    assert s.cwd == "/proj"
    assert s.context_window == 200000
    assert s.started_at_ms > 0


def test_session_info_reply_updates_context_tokens_and_usage():
    s = _new()
    apply(
        s,
        {
            "type": "blemeesd.session_info_reply",
            "session_id": "s1",
            "context_tokens": 14123,
            "model": "claude-sonnet-4-6",
            "cumulative_usage": {"input_tokens": 100, "output_tokens": 200},
            "last_seq": 99,
            "last_turn_at_ms": 1745000000000,
        },
    )
    assert s.context_tokens == 14123
    assert s.cumulative_usage.input_tokens == 100
    assert s.last_seq == 99
    assert s.last_active_at_ms == 1745000000000


def test_notice_does_not_pollute_session_options():
    """Rate-limit notices live on AppState now (§9.6); the reducer must not
    mutate session.options for them."""
    s = _new()
    apply(
        s,
        {
            "type": "agent.notice",
            "session_id": "s1",
            "level": "warn",
            "category": "rate_limits",
            "text": "resets in 4m",
            "data": {"resets_in_s": 240},
        },
    )
    assert "_notices" not in s.options


def test_user_text_seeds_title():
    s = _new()
    apply(
        s,
        {
            "type": "agent.user",
            "session_id": "s1",
            "seq": 1,
            "message": {"role": "user", "content": "  refactor   utils.py please   "},
        },
    )
    assert s.title == "refactor utils.py please"

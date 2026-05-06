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
    apply(
        s,
        {
            "type": "agent.user",
            "session_id": "s1",
            "seq": 1,
            "message": {"role": "user", "content": "hi"},
        },
    )
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
    apply(
        s,
        {
            "type": "agent.user",
            "session_id": "s1",
            "seq": 1,
            "message": {"role": "user", "content": "?"},
        },
    )
    apply(
        s,
        {"type": "agent.delta", "session_id": "s1", "seq": 2, "kind": "thinking", "text": "ponder"},
    )
    apply(
        s, {"type": "agent.delta", "session_id": "s1", "seq": 3, "kind": "text", "text": "answer"}
    )

    blocks = s.turns[-1].blocks
    assert any(isinstance(b, ThinkingBlock) and b.text == "ponder" for b in blocks)
    assert any(isinstance(b, TextBlock) and b.text == "answer" for b in blocks)


def test_tool_use_then_tool_result_pairing():
    s = _new()
    apply(
        s,
        {
            "type": "agent.user",
            "session_id": "s1",
            "seq": 1,
            "message": {"role": "user", "content": "do"},
        },
    )
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


def test_open_ack_sets_replay_target_and_apply_clears_when_caught_up():
    """``agent.opened`` carrying ``last_seq > last_seen_seq`` opens a
    replay window. The window stays open while subsequent frames advance
    ``last_seen_seq`` toward the target, and clears once we reach it."""
    s = _new()
    s.last_seen_seq = 0  # cold-start
    apply(s, {"type": "agent.opened", "session_id": "s1", "last_seq": 100})
    assert s.replay_target_seq == 100
    assert s.replay_start_seq == 0

    # Replay frames stream in — window stays open.
    apply(s, {"type": "agent.delta", "session_id": "s1", "seq": 50, "kind": "text", "text": "x"})
    assert s.replay_target_seq == 100  # still mid-replay

    # Final replay frame catches us up — window auto-clears.
    apply(s, {"type": "agent.delta", "session_id": "s1", "seq": 100, "kind": "text", "text": "y"})
    assert s.replay_target_seq == 0


def test_open_ack_with_no_replay_does_not_set_target():
    """Brand-new sessions report ``last_seq == 0`` (or absent). The reducer
    must not open a phantom replay window."""
    s = _new()
    apply(s, {"type": "agent.opened", "session_id": "s1", "last_seq": 0})
    assert s.replay_target_seq == 0


def test_streaming_text_after_tool_starts_new_block():
    """Text deltas that arrive *after* a tool_use block must not get
    appended back into the previous (now-stale) text block. The previous
    reducer searched the whole turn for the most recent text block of any
    age, so post-tool streamed text accumulated into the pre-tool block,
    and the subsequent ``agent.message`` reconcile then overwrote that
    bloated block with just its current segment — silently dropping the
    interim message between tool calls.
    """
    s = _new()
    apply(
        s,
        {
            "type": "agent.user",
            "session_id": "s1",
            "seq": 1,
            "message": {"role": "user", "content": "go"},
        },
    )
    # Pre-tool text streams in.
    apply(
        s, {"type": "agent.delta", "session_id": "s1", "seq": 2, "kind": "text", "text": "Reading."}
    )
    # Tool fires (stand-alone frame, as with --include-partial-messages).
    apply(
        s,
        {
            "type": "agent.tool_use",
            "session_id": "s1",
            "seq": 3,
            "tool_use_id": "tu1",
            "name": "Read",
            "input": {"path": "/x"},
        },
    )
    # Post-tool text streams in — must land in a *new* text block.
    apply(
        s,
        {
            "type": "agent.delta",
            "session_id": "s1",
            "seq": 4,
            "kind": "text",
            "text": "Now editing.",
        },
    )

    blocks = s.turns[-1].blocks
    shapes = [type(b).__name__ for b in blocks]
    assert shapes == ["TextBlock", "ToolUseBlock", "TextBlock"], shapes
    assert blocks[0].text == "Reading."  # type: ignore[attr-defined]
    assert blocks[2].text == "Now editing."  # type: ignore[attr-defined]


def test_multi_message_turn_preserves_inline_text_tool_order():
    """A multi-step assistant turn (text → tool → text → tool → text)
    arrives as several ``agent.message`` frames. Each message must append
    its content rather than overwrite the previous text block, and each
    tool_use must land at its inline position in the content array
    rather than getting shoved to the tail. The previous reducer
    collapsed all text into block 0 and clustered tools at the bottom.
    """
    s = _new()
    apply(
        s,
        {
            "type": "agent.user",
            "session_id": "s1",
            "seq": 1,
            "message": {"role": "user", "content": "do it"},
        },
    )

    apply(
        s,
        {
            "type": "agent.message",
            "session_id": "s1",
            "seq": 2,
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Reading the file."},
                {
                    "type": "tool_use",
                    "id": "tu_read",
                    "name": "Read",
                    "input": {"path": "/x"},
                },
            ],
        },
    )
    apply(
        s,
        {
            "type": "agent.tool_result",
            "session_id": "s1",
            "seq": 3,
            "tool_use_id": "tu_read",
            "output": "...",
        },
    )

    apply(
        s,
        {
            "type": "agent.message",
            "session_id": "s1",
            "seq": 4,
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Now editing."},
                {
                    "type": "tool_use",
                    "id": "tu_edit",
                    "name": "Edit",
                    "input": {"path": "/x", "old_string": "a", "new_string": "b"},
                },
            ],
        },
    )
    apply(
        s,
        {
            "type": "agent.tool_result",
            "session_id": "s1",
            "seq": 5,
            "tool_use_id": "tu_edit",
            "output": "ok",
        },
    )

    apply(
        s,
        {
            "type": "agent.message",
            "session_id": "s1",
            "seq": 6,
            "role": "assistant",
            "content": [{"type": "text", "text": "Done."}],
        },
    )

    blocks = s.turns[-1].blocks
    # Expected inline order: text, tool, text, tool, text — five blocks total.
    shapes = [type(b).__name__ for b in blocks]
    assert shapes == [
        "TextBlock",
        "ToolUseBlock",
        "TextBlock",
        "ToolUseBlock",
        "TextBlock",
    ], shapes
    assert blocks[0].text == "Reading the file."  # type: ignore[attr-defined]
    assert blocks[1].name == "Read"  # type: ignore[attr-defined]
    assert blocks[2].text == "Now editing."  # type: ignore[attr-defined]
    assert blocks[3].name == "Edit"  # type: ignore[attr-defined]
    assert blocks[4].text == "Done."  # type: ignore[attr-defined]


def test_tool_use_inside_agent_message_content_is_extracted():
    """Claude Code suppresses ``stream_event{content_block_*}`` by default,
    so tool calls arrive only embedded in ``agent.message.content`` — not
    as standalone ``agent.tool_use`` frames. The reducer must extract them
    or tool calls never appear in the transcript.
    """
    s = _new()
    apply(
        s,
        {
            "type": "agent.user",
            "session_id": "s1",
            "seq": 1,
            "message": {"role": "user", "content": "read it"},
        },
    )
    apply(
        s,
        {
            "type": "agent.message",
            "session_id": "s1",
            "seq": 2,
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Reading the file."},
                {
                    "type": "tool_use",
                    "id": "toolu_xyz",
                    "name": "Read",
                    "input": {"path": "/tmp/foo"},
                },
            ],
        },
    )
    blocks = s.turns[-1].blocks
    tool = next(b for b in blocks if isinstance(b, ToolUseBlock))
    assert tool.tool_use_id == "toolu_xyz"
    assert tool.name == "Read"
    assert tool.input == {"path": "/tmp/foo"}


def test_tool_use_in_message_dedupes_against_standalone_frame():
    """If both paths fire (CC with --include-partial-messages), the
    standalone ``agent.tool_use`` arrives first with empty input, then
    ``agent.message`` carries the full input. Dedupe by id and upgrade
    the input rather than creating a duplicate block."""
    s = _new()
    apply(
        s,
        {
            "type": "agent.user",
            "session_id": "s1",
            "seq": 1,
            "message": {"role": "user", "content": "read it"},
        },
    )
    apply(
        s,
        {
            "type": "agent.tool_use",
            "session_id": "s1",
            "seq": 2,
            "tool_use_id": "toolu_xyz",
            "name": "Read",
            "input": {},
        },
    )
    apply(
        s,
        {
            "type": "agent.message",
            "session_id": "s1",
            "seq": 3,
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_xyz",
                    "name": "Read",
                    "input": {"path": "/tmp/foo"},
                }
            ],
        },
    )
    tools = [b for b in s.turns[-1].blocks if isinstance(b, ToolUseBlock)]
    assert len(tools) == 1
    assert tools[0].input == {"path": "/tmp/foo"}


def test_tool_use_with_list_input_does_not_crash():
    """Codex's ``exec_command_begin`` translates to ``agent.tool_use`` with
    ``input`` as a shell argv list. The reducer must accept any JSON shape
    — coercing to dict() previously raised ValueError and the connection
    layer swallowed the exception, so tool calls never appeared.
    """
    s = _new()
    apply(
        s,
        {
            "type": "agent.user",
            "session_id": "s1",
            "seq": 1,
            "message": {"role": "user", "content": "ls"},
        },
    )
    apply(
        s,
        {
            "type": "agent.tool_use",
            "session_id": "s1",
            "seq": 2,
            "tool_use_id": "tu1",
            "name": "shell",
            "input": ["bash", "-c", "ls"],
        },
    )
    block = next(b for b in s.turns[-1].blocks if isinstance(b, ToolUseBlock))
    assert block.name == "shell"
    assert block.input == ["bash", "-c", "ls"]


def test_session_taken_flips_to_detached():
    s = _new()
    apply(s, {"type": "agent.session_taken", "session_id": "s1", "by_peer_pid": 99})
    assert s.mode == SessionMode.DETACHED
    assert s.taken_by_pid == 99


def test_replay_gap_marks_session():
    s = _new()
    apply(s, {"type": "agent.replay_gap", "session_id": "s1"})
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
            "type": "agent.session_info_reply",
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

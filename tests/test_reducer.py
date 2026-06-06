"""Reducer tests — blemees/3 (#1).

The reducer consumes ``session.update`` (verbatim ACP payloads),
``session.result``, ``session.opened`` and the per-session lifecycle notices.
These cover the #1 text path; the richer session/update vocabulary (tool
calls, plan, commands, mode) lands with its tests in #2.
"""

from __future__ import annotations

from blemees_tui.reducer import apply, apply_user_prompt
from blemees_tui.state import SessionMode, SessionState, TextBlock, ThinkingBlock, ToolUseBlock


def _new() -> SessionState:
    return SessionState(session_id="s1", backend="default")


def _chunk(seq: int, text: str, *, thought: bool = False) -> dict:
    kind = "agent_thought_chunk" if thought else "agent_message_chunk"
    return {
        "type": "session.update",
        "session_id": "s1",
        "seq": seq,
        "update": {"sessionUpdate": kind, "content": {"type": "text", "text": text}},
    }


# ---- user prompt (recorded locally; not echoed by the daemon) -------


def test_apply_user_prompt_creates_turn_and_title():
    s = _new()
    apply_user_prompt(s, "refactor utils.py please")
    assert len(s.turns) == 1
    assert s.turns[0].user_text == "refactor utils.py please"
    assert s.turn_active is True
    assert s.title == "refactor utils.py please"


# ---- streamed assistant text ----------------------------------------


def test_message_chunks_extend_one_text_block():
    s = _new()
    apply_user_prompt(s, "hi")
    apply(s, _chunk(2, "hel"))
    apply(s, _chunk(3, "lo"))
    turn = s.turns[-1]
    assert len(turn.blocks) == 1
    assert isinstance(turn.blocks[0], TextBlock)
    assert turn.blocks[0].text == "hello"


def test_thought_chunk_creates_separate_thinking_block():
    s = _new()
    apply_user_prompt(s, "hi")
    apply(s, _chunk(2, "pondering", thought=True))
    apply(s, _chunk(3, "answer"))
    turn = s.turns[-1]
    assert isinstance(turn.blocks[0], ThinkingBlock)
    assert turn.blocks[0].text == "pondering"
    assert isinstance(turn.blocks[1], TextBlock)
    assert turn.blocks[1].text == "answer"


def test_update_without_active_turn_opens_one():
    # A viewer attaching mid-turn sees chunks with no local user turn.
    s = _new()
    apply(s, _chunk(5, "streamed"))
    assert len(s.turns) == 1
    assert s.turns[0].blocks[0].text == "streamed"


# ---- turn end + usage ------------------------------------------------


def test_result_locks_turn_and_maps_acp_usage():
    s = _new()
    apply_user_prompt(s, "hi")
    apply(s, _chunk(2, "done"))
    apply(
        s,
        {
            "type": "session.result",
            "session_id": "s1",
            "seq": 3,
            "stop_reason": "end_turn",
            # ACP usage is camelCase.
            "usage": {"inputTokens": 10, "outputTokens": 5, "cachedReadTokens": 2},
        },
    )
    turn = s.turns[-1]
    assert turn.locked is True
    assert turn.result_subtype == "end_turn"
    assert s.turn_active is False
    assert turn.usage.input_tokens == 10
    assert turn.usage.output_tokens == 5
    assert turn.usage.cache_read_input_tokens == 2
    # Cumulative merge.
    assert s.cumulative_usage.input_tokens == 10


def test_result_without_usage_still_locks():
    s = _new()
    apply_user_prompt(s, "hi")
    apply(s, {"type": "session.result", "session_id": "s1", "seq": 2, "stop_reason": "cancelled"})
    assert s.turns[-1].locked is True
    assert s.turns[-1].result_subtype == "cancelled"


# ---- replay window (session.opened) ----------------------------------


def test_opened_sets_replay_target_then_clears_when_caught_up():
    s = _new()
    s.last_seen_seq = 2
    apply(s, {"type": "session.opened", "session_id": "s1", "last_seq": 5})
    assert s.replay_target_seq == 5
    assert s.replay_start_seq == 2
    # Catching up to the target clears the window.
    apply(s, _chunk(5, "caught up"))
    assert s.replay_target_seq == 0


def test_opened_caught_up_no_replay_window():
    s = _new()
    s.last_seen_seq = 5
    apply(s, {"type": "session.opened", "session_id": "s1", "last_seq": 5})
    assert s.replay_target_seq == 0


# ---- lifecycle notices ----------------------------------------------


def test_session_error_agent_crashed_flips_to_crashed():
    s = _new()
    apply(
        s,
        {"type": "session.error", "session_id": "s1", "code": "agent_crashed", "message": "boom"},
    )
    assert s.mode == SessionMode.CRASHED
    assert s.crashed_reason == "boom"
    assert s.pending_errors and s.pending_errors[0]["code"] == "agent_crashed"


def test_session_taken_flips_to_detached():
    s = _new()
    apply(s, {"type": "session.taken", "session_id": "s1", "by_peer_pid": 4321})
    assert s.mode == SessionMode.DETACHED
    assert s.taken_by_pid == 4321


def test_session_closed_notice_flips_to_closed():
    s = _new()
    apply(s, {"type": "session.closed_notice", "session_id": "s1", "reason": "owner_closed"})
    assert s.mode == SessionMode.CLOSED
    assert s.closed_reason == "owner_closed"


def test_replay_gap_marks_session():
    s = _new()
    apply(s, {"type": "replay_gap", "session_id": "s1", "since_seq": 1, "first_available_seq": 9})
    assert s.replay_gap is True


def test_session_info_reply_updates_context_and_usage():
    s = _new()
    apply(
        s,
        {
            "type": "session.info_reply",
            "session_id": "s1",
            "context_tokens": 1234,
            "model": "sonnet",
            "cwd": "/proj",
            "cumulative_usage": {"input_tokens": 100, "output_tokens": 50},
        },
    )
    assert s.context_tokens == 1234
    assert s.model == "sonnet"
    assert s.cwd == "/proj"
    assert s.cumulative_usage.input_tokens == 100


def test_unknown_frame_type_is_ignored():
    s = _new()
    apply(s, {"type": "totally.unknown", "session_id": "s1", "seq": 1})
    assert s.turns == []


# ---- #2: full ACP session/update vocabulary ------------------------


def _update(seq: int, update: dict) -> dict:
    return {"type": "session.update", "session_id": "s1", "seq": seq, "update": update}


def test_tool_call_creates_block_with_status():
    s = _new()
    apply_user_prompt(s, "do it")
    apply(
        s,
        _update(
            2,
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "t1",
                "title": "Read file",
                "kind": "read",
                "status": "pending",
                "rawInput": {"path": "/a"},
            },
        ),
    )
    block = s.turns[-1].blocks[-1]
    assert isinstance(block, ToolUseBlock)
    assert block.tool_use_id == "t1"
    assert block.status == "pending"
    assert block.kind == "read"
    assert block.input == {"path": "/a"}


def test_tool_call_update_transitions_status_and_captures_output():
    s = _new()
    apply_user_prompt(s, "go")
    apply(s, _update(2, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Run"}))
    apply(
        s,
        _update(
            3,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "t1",
                "status": "completed",
                "content": [{"type": "content", "content": {"type": "text", "text": "done"}}],
            },
        ),
    )
    block = s.turns[-1].blocks[-1]
    assert block.status == "completed"
    assert block.is_error is False
    assert block.result_text == "done"


def test_tool_call_update_failed_marks_error():
    s = _new()
    apply_user_prompt(s, "go")
    apply(s, _update(2, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Run"}))
    apply(
        s, _update(3, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "failed"})
    )
    block = s.turns[-1].blocks[-1]
    assert block.status == "failed"
    assert block.is_error is True


def test_tool_call_update_before_start_synthesizes_block():
    s = _new()
    apply_user_prompt(s, "go")
    apply(
        s,
        _update(
            2, {"sessionUpdate": "tool_call_update", "toolCallId": "t9", "status": "in_progress"}
        ),
    )
    block = s.turns[-1].blocks[-1]
    assert isinstance(block, ToolUseBlock) and block.tool_use_id == "t9"


def test_plan_populates_session_plan():
    s = _new()
    apply(
        s,
        _update(
            1,
            {
                "sessionUpdate": "plan",
                "entries": [
                    {"content": "step one", "status": "in_progress", "priority": "high"},
                    {"content": "step two", "status": "pending", "priority": "low"},
                ],
            },
        ),
    )
    assert [e["content"] for e in s.plan] == ["step one", "step two"]
    assert s.plan[0]["status"] == "in_progress"


def test_available_commands_update_populates_commands():
    s = _new()
    apply(
        s,
        _update(
            1,
            {
                "sessionUpdate": "available_commands_update",
                "availableCommands": [
                    {"name": "compact", "description": "compress history"},
                    {"name": "review"},
                ],
            },
        ),
    )
    assert [c["name"] for c in s.available_commands] == ["compact", "review"]
    assert s.available_commands[0]["description"] == "compress history"


def test_current_mode_update_sets_mode():
    s = _new()
    apply(s, _update(1, {"sessionUpdate": "current_mode_update", "currentModeId": "plan"}))
    assert s.current_mode == "plan"


# ---- #3: view_only + needs_attention -------------------------------


def test_opened_sets_view_only():
    s = _new()
    apply(s, {"type": "session.opened", "session_id": "s1", "view_only": True})
    assert s.view_only is True


def test_needs_attention_then_cleared():
    s = _new()
    apply(
        s,
        {"type": "session.needs_attention", "session_id": "s1", "reason": "permission_pending"},
    )
    assert s.needs_attention is True
    assert s.attention_reason == "permission_pending"
    apply(s, {"type": "session.attention_cleared", "session_id": "s1"})
    assert s.needs_attention is False
    assert s.attention_reason is None

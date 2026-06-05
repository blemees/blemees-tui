"""Reducer tests — blemees/3 (#1).

The reducer consumes ``session.update`` (verbatim ACP payloads),
``session.result``, ``session.opened`` and the per-session lifecycle notices.
These cover the #1 text path; the richer session/update vocabulary (tool
calls, plan, commands, mode) lands with its tests in #2.
"""

from __future__ import annotations

from blemees_tui.reducer import apply, apply_user_prompt
from blemees_tui.state import SessionMode, SessionState, TextBlock, ThinkingBlock


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

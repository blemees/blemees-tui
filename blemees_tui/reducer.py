"""Pure reducer: ``(SessionState, Frame) → SessionState`` (spec §4, §9).

The reducer is the single source of truth for transcript state. It mutates a
``SessionState`` in place (instead of building new dataclasses for every
frame) — the dataclasses are not frozen and mutation keeps streaming-delta
hot paths cheap. Callers should treat the reducer's input as owned for the
duration of the call.

The reducer touches **only** the ``SessionState``. ``EventLog`` writes,
sidebar refresh, and persistence are the connection layer's responsibility.
"""

from __future__ import annotations

import time
from typing import Any

from .state import (
    SessionMode,
    SessionState,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Turn,
    Usage,
)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def apply(state: SessionState, frame: dict[str, Any]) -> SessionState:
    """Apply a single wire frame to ``state``. Returns the same instance."""
    ftype = frame.get("type", "")
    if not ftype:
        return state

    seq = frame.get("seq")
    if isinstance(seq, int) and seq > state.last_seq:
        state.last_seq = seq
        state.last_seen_seq = seq

    handler = _HANDLERS.get(ftype)
    if handler is not None:
        handler(state, frame)
    return state


# ---------------------------------------------------------------------------
# Handlers — agent.* (unified vocabulary, spec §9)
# ---------------------------------------------------------------------------


def _on_system_init(state: SessionState, frame: dict[str, Any]) -> None:
    if frame.get("model"):
        state.model = frame["model"]
    if frame.get("cwd"):
        state.cwd = frame["cwd"]
    cw = frame.get("context_window")
    if isinstance(cw, int) and cw > 0:
        state.context_window = cw
    if frame.get("backend"):
        state.backend = frame["backend"]
    if not state.started_at_ms:
        state.started_at_ms = int(time.time() * 1000)


def _ensure_active_turn(state: SessionState) -> Turn:
    """Return the current in-flight turn, creating one if the backend skipped a user echo."""
    if state.turns and not state.turns[-1].locked:
        return state.turns[-1]
    turn = Turn()
    state.turns.append(turn)
    state.turn_active = True
    return turn


def _on_user(state: SessionState, frame: dict[str, Any]) -> None:
    msg = frame.get("message") or {}
    content = msg.get("content")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        # Concatenate text blocks; ignore images/attachments at v0.1.
        chunks: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                chunks.append(str(block.get("text", "")))
        text = "\n".join(chunks)
    turn = Turn(user_text=text)
    state.turns.append(turn)
    state.turn_active = True
    if not state.title and text:
        # First-message-derived title (first 80 chars, whitespace-collapsed).
        flat = " ".join(text.split())
        state.title = flat[:80]


def _on_delta(state: SessionState, frame: dict[str, Any]) -> None:
    kind = frame.get("kind")
    text = frame.get("text", "")
    if not text:
        return
    turn = _ensure_active_turn(state)
    if kind == "thinking":
        block = _last_block_of_type(turn, ThinkingBlock)
        if block is None or block.finalized:
            block = ThinkingBlock()
            turn.blocks.append(block)
        block.text += text
    elif kind == "text":
        block = _last_block_of_type(turn, TextBlock)
        if block is None or block.finalized:
            block = TextBlock()
            turn.blocks.append(block)
        block.text += text
    # tool_input deltas are ignored at v0.1 — agent.tool_use carries the
    # final input dict and the daemon resolves partial_json before emitting.


def _on_message(state: SessionState, frame: dict[str, Any]) -> None:
    """Replace the in-progress text/thinking blocks with canonical content."""
    content = frame.get("content") or []
    turn = _ensure_active_turn(state)
    # The daemon may emit multiple agent.message frames per turn (one per
    # streamed message). We finalise whatever text/thinking blocks are
    # currently open; tool_use blocks stay where they are.
    for block in turn.blocks:
        if isinstance(block, (TextBlock, ThinkingBlock)) and not block.finalized:
            block.finalized = True

    canonical_text = _join_text_blocks(content)
    if canonical_text:
        # Find the most recent open text block and overwrite — handles cases
        # where deltas accumulated out of order.
        text_blocks = [b for b in turn.blocks if isinstance(b, TextBlock)]
        if text_blocks:
            text_blocks[-1].text = canonical_text
            text_blocks[-1].finalized = True
        else:
            turn.blocks.append(TextBlock(text=canonical_text, finalized=True))


def _on_tool_use(state: SessionState, frame: dict[str, Any]) -> None:
    tool_use_id = frame.get("tool_use_id") or ""
    if not tool_use_id:
        return
    turn = _ensure_active_turn(state)
    turn.blocks.append(
        ToolUseBlock(
            tool_use_id=tool_use_id,
            name=str(frame.get("name", "")),
            input=dict(frame.get("input") or {}),
        )
    )


def _on_tool_result(state: SessionState, frame: dict[str, Any]) -> None:
    tool_use_id = frame.get("tool_use_id") or ""
    if not tool_use_id:
        return
    output = frame.get("output")
    is_error = bool(frame.get("is_error", False))
    text = _stringify_tool_output(output)
    for turn in reversed(state.turns):
        for block in turn.blocks:
            if isinstance(block, ToolUseBlock) and block.tool_use_id == tool_use_id:
                block.result_text = text
                block.is_error = is_error
                return


def _on_notice(_state: SessionState, _frame: dict[str, Any]) -> None:
    # The reducer is session-scoped; rate_limits/notices are app-level.
    # The connection-layer pump in app.py forwards these to AppState.
    return


def _on_result(state: SessionState, frame: dict[str, Any]) -> None:
    if not state.turns:
        return
    turn = state.turns[-1]
    turn.locked = True
    turn.result_subtype = frame.get("subtype")
    if turn.result_subtype == "error":
        turn.error = dict(frame.get("error") or {})
    if isinstance(frame.get("duration_ms"), int):
        turn.duration_ms = frame["duration_ms"]
    usage_raw = frame.get("usage") or {}
    if isinstance(usage_raw, dict):
        turn_usage = Usage(
            input_tokens=int(usage_raw.get("input_tokens", 0) or 0),
            output_tokens=int(usage_raw.get("output_tokens", 0) or 0),
            cache_creation_input_tokens=int(
                usage_raw.get("cache_creation_input_tokens", 0) or 0
            ),
            cache_read_input_tokens=int(usage_raw.get("cache_read_input_tokens", 0) or 0),
            reasoning_output_tokens=int(usage_raw.get("reasoning_output_tokens", 0) or 0),
        )
        turn.usage = turn_usage
        state.cumulative_usage = state.cumulative_usage.merge(turn_usage)
    state.turn_active = False


# ---------------------------------------------------------------------------
# Handlers — per-session blemeesd.* (spec §15)
# ---------------------------------------------------------------------------


def _on_session_taken(state: SessionState, frame: dict[str, Any]) -> None:
    state.mode = SessionMode.DETACHED
    pid = frame.get("by_peer_pid")
    state.taken_by_pid = int(pid) if isinstance(pid, int) else None


def _on_session_closed(state: SessionState, frame: dict[str, Any]) -> None:
    state.mode = SessionMode.CLOSED
    state.closed_reason = str(frame.get("reason", "owner_closed"))


def _on_replay_gap(state: SessionState, _frame: dict[str, Any]) -> None:
    state.replay_gap = True


def _on_error(state: SessionState, frame: dict[str, Any]) -> None:
    code = str(frame.get("code", ""))
    if code == "backend_crashed":
        state.mode = SessionMode.CRASHED
        state.crashed_reason = str(frame.get("message", ""))
    state.pending_errors.append(dict(frame))


def _on_session_info_reply(state: SessionState, frame: dict[str, Any]) -> None:
    ctx = frame.get("context_tokens")
    if isinstance(ctx, int) and ctx >= 0:
        state.context_tokens = ctx
    if frame.get("model"):
        state.model = str(frame["model"])
    if frame.get("cwd"):
        state.cwd = str(frame["cwd"])
    last_seq = frame.get("last_seq")
    if isinstance(last_seq, int) and last_seq > state.last_seq:
        state.last_seq = last_seq
    last_turn = frame.get("last_turn_at_ms")
    if isinstance(last_turn, int) and last_turn > 0:
        state.last_active_at_ms = last_turn
    cumulative_raw = frame.get("cumulative_usage")
    if isinstance(cumulative_raw, dict):
        state.cumulative_usage = Usage(
            input_tokens=int(cumulative_raw.get("input_tokens", 0) or 0),
            output_tokens=int(cumulative_raw.get("output_tokens", 0) or 0),
            cache_creation_input_tokens=int(
                cumulative_raw.get("cache_creation_input_tokens", 0) or 0
            ),
            cache_read_input_tokens=int(cumulative_raw.get("cache_read_input_tokens", 0) or 0),
            reasoning_output_tokens=int(cumulative_raw.get("reasoning_output_tokens", 0) or 0),
        )


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------


_HANDLERS = {
    "agent.system_init": _on_system_init,
    "agent.user": _on_user,
    "agent.user_echo": lambda *_: None,  # Spec §7.2: TUI never relies on user_echo.
    "agent.delta": _on_delta,
    "agent.message": _on_message,
    "agent.tool_use": _on_tool_use,
    "agent.tool_result": _on_tool_result,
    "agent.notice": _on_notice,
    "agent.result": _on_result,
    "blemeesd.session_taken": _on_session_taken,
    "blemeesd.session_closed": _on_session_closed,
    "blemeesd.replay_gap": _on_replay_gap,
    "blemeesd.session_info_reply": _on_session_info_reply,
    "blemeesd.error": _on_error,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _last_block_of_type(turn: Turn, cls: type):
    for block in reversed(turn.blocks):
        if isinstance(block, cls):
            return block
    return None


def _join_text_blocks(content: list[Any]) -> str:
    chunks: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            chunks.append(str(block.get("text", "")))
    return "".join(chunks)


def _stringify_tool_output(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        chunks: list[str] = []
        for block in output:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    chunks.append(str(block.get("text", "")))
                elif "text" in block:
                    chunks.append(str(block["text"]))
        return "\n".join(chunks)
    return str(output)

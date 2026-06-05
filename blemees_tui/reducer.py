"""Pure reducer: ``(SessionState, Frame) → SessionState`` (blemees/3).

The reducer is the single source of truth for transcript state. It mutates a
``SessionState`` in place — the dataclasses are not frozen and mutation keeps
streaming-delta hot paths cheap. Callers treat the reducer's input as owned
for the duration of the call.

It consumes ``blemees/3`` frames: ``session.update`` (a verbatim ACP
``session/update`` payload), ``session.result``, ``session.opened``,
``session.error``, and the per-session lifecycle notices. The user turn is
*not* echoed by the daemon, so the app records it locally via
:func:`apply_user_prompt`.

This issue (#1) handles the text path — ``agent_message_chunk`` /
``agent_thought_chunk``. The richer ``session/update`` variants (tool calls,
plan, available commands, mode) are #2.
"""

from __future__ import annotations

from typing import Any

from acp.schema import TextContent

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
# Public entry points
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

    # Clear the replay window once we've caught up to the daemon's
    # high-water mark so the chat pane drops the loading overlay.
    if state.replay_target_seq and state.last_seen_seq >= state.replay_target_seq:
        state.replay_target_seq = 0
        state.replay_start_seq = 0
    return state


def apply_user_prompt(state: SessionState, text: str) -> SessionState:
    """Record a user turn the client just sent.

    blemees/3 agents don't echo the user message back as a frame, so the TUI
    appends the turn optimistically when it sends ``session.prompt``.
    """
    turn = Turn(user_text=text)
    state.turns.append(turn)
    state.turn_active = True
    if not state.title and text:
        state.title = " ".join(text.split())[:80]
    return state


# ---------------------------------------------------------------------------
# session.update — verbatim ACP session/update payload
# ---------------------------------------------------------------------------


def _ensure_active_turn(state: SessionState) -> Turn:
    """Return the current in-flight turn, creating one if needed."""
    if state.turns and not state.turns[-1].locked:
        return state.turns[-1]
    turn = Turn()
    state.turns.append(turn)
    state.turn_active = True
    return turn


def _content_text(content: Any) -> str:
    """Extract text from an ACP content block, typed via the SDK model."""
    if not isinstance(content, dict):
        return ""
    if content.get("type") == "text":
        try:
            return TextContent.model_validate(content).text
        except Exception:
            return str(content.get("text", "") or "")
    return ""  # non-text blocks (image, resource, …) render in #2


def _append_streamed(turn: Turn, text: str, *, thinking: bool) -> None:
    """Extend the open block of the matching kind, or open a fresh one."""
    cls = ThinkingBlock if thinking else TextBlock
    last = turn.blocks[-1] if turn.blocks else None
    if isinstance(last, cls) and not last.finalized:
        last.text += text
    else:
        turn.blocks.append(cls(text=text))


def _find_tool(state: SessionState, tool_call_id: str) -> ToolUseBlock | None:
    for turn in reversed(state.turns):
        for block in turn.blocks:
            if isinstance(block, ToolUseBlock) and block.tool_use_id == tool_call_id:
                return block
    return None


def _tool_content_text(content: Any) -> str:
    """Flatten ACP ToolCallContent[] into display text (#2).

    Each item is ``{type: "content"|"diff"|"terminal", …}``. We surface the
    text-bearing parts; richer diff/terminal rendering can deepen later.
    """
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "content":
            parts.append(_content_text(item.get("content")))
        elif item.get("type") == "diff":
            path = item.get("path", "")
            parts.append(f"diff: {path}" if path else "diff")
        elif item.get("type") == "terminal":
            parts.append(_content_text(item.get("content")) or "terminal output")
    return "\n".join(p for p in parts if p)


def _on_session_update(state: SessionState, frame: dict[str, Any]) -> None:
    update = frame.get("update")
    if not isinstance(update, dict):
        return
    kind = update.get("sessionUpdate")
    if kind == "agent_message_chunk":
        text = _content_text(update.get("content"))
        if text:
            _append_streamed(_ensure_active_turn(state), text, thinking=False)
    elif kind == "agent_thought_chunk":
        text = _content_text(update.get("content"))
        if text:
            _append_streamed(_ensure_active_turn(state), text, thinking=True)
    elif kind == "tool_call":
        _on_tool_call(state, update)
    elif kind == "tool_call_update":
        _on_tool_call_update(state, update)
    elif kind == "plan":
        state.plan = _plan_entries(update.get("entries"))
    elif kind == "available_commands_update":
        state.available_commands = _commands(update.get("availableCommands"))
    elif kind == "current_mode_update":
        mode_id = update.get("currentModeId")
        if isinstance(mode_id, str):
            state.current_mode = mode_id
    # user_message_chunk (echoed user turns on replay) is recorded locally
    # via apply_user_prompt, so it's ignored here.


def _on_tool_call(state: SessionState, update: dict[str, Any]) -> None:
    tool_call_id = update.get("toolCallId")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        return
    turn = _ensure_active_turn(state)
    existing = _find_tool(state, tool_call_id)
    block = existing or ToolUseBlock(tool_call_id, name="", input=None)
    block.title = str(update.get("title") or block.title)
    block.name = block.title or str(update.get("kind") or block.name)
    block.kind = str(update.get("kind") or block.kind)
    if update.get("status"):
        block.status = str(update["status"])
    if update.get("rawInput") is not None:
        block.input = update["rawInput"]
    content_text = _tool_content_text(update.get("content"))
    if content_text:
        block.result_text = content_text
    if existing is None:
        turn.blocks.append(block)


def _on_tool_call_update(state: SessionState, update: dict[str, Any]) -> None:
    tool_call_id = update.get("toolCallId")
    if not isinstance(tool_call_id, str):
        return
    block = _find_tool(state, tool_call_id)
    if block is None:
        # An update before the start — synthesize the block so we don't drop it.
        _on_tool_call(state, update)
        return
    if update.get("status"):
        block.status = str(update["status"])
        block.is_error = block.status == "failed"
    if update.get("title"):
        block.title = str(update["title"])
    if update.get("rawInput") is not None:
        block.input = update["rawInput"]
    content_text = _tool_content_text(update.get("content"))
    if content_text:
        block.result_text = content_text


def _plan_entries(entries: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict) and e.get("content"):
                out.append(
                    {
                        "content": str(e.get("content", "")),
                        "status": str(e.get("status", "pending")),
                        "priority": str(e.get("priority", "medium")),
                    }
                )
    return out


def _commands(commands: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(commands, list):
        for c in commands:
            if isinstance(c, dict) and c.get("name"):
                out.append(
                    {"name": str(c["name"]), "description": str(c.get("description", "") or "")}
                )
    return out


def _map_usage(raw: dict[str, Any]) -> Usage:
    """Map an ACP Usage payload to the TUI's Usage.

    ACP reports camelCase (``inputTokens``, ``cachedReadTokens``, …); we read
    those plus snake_case fallbacks so a partial payload (the SDK model marks
    several fields required) never drops counts.
    """

    def pick(*keys: str) -> int:
        for k in keys:
            if k in raw and raw[k] is not None:
                try:
                    return int(raw[k])
                except (TypeError, ValueError):
                    return 0
        return 0

    return Usage(
        input_tokens=pick("inputTokens", "input_tokens"),
        output_tokens=pick("outputTokens", "output_tokens"),
        cache_creation_input_tokens=pick("cachedWriteTokens", "cache_creation_input_tokens"),
        cache_read_input_tokens=pick("cachedReadTokens", "cache_read_input_tokens"),
        reasoning_output_tokens=pick("thoughtTokens", "reasoning_output_tokens"),
    )


def _on_result(state: SessionState, frame: dict[str, Any]) -> None:
    if not state.turns:
        return
    turn = state.turns[-1]
    turn.locked = True
    # ACP stop_reason: end_turn | cancelled | max_tokens | refusal | …
    stop = frame.get("stop_reason")
    turn.result_subtype = str(stop) if stop is not None else None
    usage_raw = frame.get("usage")
    if isinstance(usage_raw, dict):
        turn_usage = _map_usage(usage_raw)
        turn.usage = turn_usage
        state.cumulative_usage = state.cumulative_usage.merge(turn_usage)
    state.turn_active = False


def _on_session_error(state: SessionState, frame: dict[str, Any]) -> None:
    code = str(frame.get("code", ""))
    if code == "agent_crashed":
        state.mode = SessionMode.CRASHED
        state.crashed_reason = str(frame.get("message", ""))
    state.pending_errors.append(dict(frame))


# ---------------------------------------------------------------------------
# Lifecycle / control frames
# ---------------------------------------------------------------------------


def _on_session_opened(state: SessionState, frame: dict[str, Any]) -> None:
    """``session.opened`` carries the agent's metadata plus ``last_seq`` (the
    daemon's high-water mark). If we're behind, open a replay window so the UI
    shows a loading overlay until we catch up.
    """
    if frame.get("profile"):
        state.backend = str(frame["profile"])  # repurposed label until #2/#3
    if frame.get("model"):
        state.model = str(frame["model"])
    last_seq = frame.get("last_seq")
    if not isinstance(last_seq, int) or last_seq <= state.last_seen_seq:
        state.replay_target_seq = 0
        state.replay_start_seq = 0
        return
    state.replay_target_seq = last_seq
    state.replay_start_seq = state.last_seen_seq


def _on_session_taken(state: SessionState, frame: dict[str, Any]) -> None:
    state.mode = SessionMode.DETACHED
    pid = frame.get("by_peer_pid")
    state.taken_by_pid = int(pid) if isinstance(pid, int) else None


def _on_session_closed_notice(state: SessionState, frame: dict[str, Any]) -> None:
    state.mode = SessionMode.CLOSED
    state.closed_reason = str(frame.get("reason", "owner_closed"))


def _on_replay_gap(state: SessionState, _frame: dict[str, Any]) -> None:
    state.replay_gap = True


_KNOWN_WINDOW_TIERS = (200_000, 1_000_000, 2_000_000)


def _round_up_window(tokens: int) -> int:
    for tier in _KNOWN_WINDOW_TIERS:
        if tokens <= tier:
            return tier
    return tokens


def _on_session_info_reply(state: SessionState, frame: dict[str, Any]) -> None:
    ctx = frame.get("context_tokens")
    if isinstance(ctx, int) and ctx >= 0:
        state.context_tokens = ctx
        if state.context_window and ctx > state.context_window:
            state.context_window = _round_up_window(ctx)
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
    "session.update": _on_session_update,
    "session.result": _on_result,
    "session.error": _on_session_error,
    "session.opened": _on_session_opened,
    "session.attached": _on_session_opened,  # same replay-window logic
    "session.taken": _on_session_taken,
    "session.closed_notice": _on_session_closed_notice,
    "replay_gap": _on_replay_gap,
    "session.info_reply": _on_session_info_reply,
}


__all__ = ["apply", "apply_user_prompt"]

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

    # Clear the replay window once we've caught up to the daemon's
    # high-water mark so the chat pane drops the loading overlay.
    if state.replay_target_seq and state.last_seen_seq >= state.replay_target_seq:
        state.replay_target_seq = 0
        state.replay_start_seq = 0
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
    # Only extend the *last* block when it's still the open block of the
    # right kind. Searching the whole turn (the prior approach) routed
    # text-after-a-tool deltas back into the *previous* text block, which
    # then got squashed when ``agent.message`` reconciled.
    if kind == "thinking":
        last = turn.blocks[-1] if turn.blocks else None
        if isinstance(last, ThinkingBlock) and not last.finalized:
            last.text += text
        else:
            turn.blocks.append(ThinkingBlock(text=text))
    elif kind == "text":
        last = turn.blocks[-1] if turn.blocks else None
        if isinstance(last, TextBlock) and not last.finalized:
            last.text += text
        else:
            turn.blocks.append(TextBlock(text=text))
    # tool_input deltas are ignored at v0.1 — agent.tool_use carries the
    # final input dict and the daemon resolves partial_json before emitting.


def _on_message(state: SessionState, frame: dict[str, Any]) -> None:
    """Reconcile the turn's blocks with this message's canonical content,
    preserving inline order between text and tool_use items.

    The wire shape (Anthropic-style content array) is e.g.::

        [
            {"type": "text", "text": "Reading the file."},
            {"type": "tool_use", "id": "tu1", "name": "Read", "input": {...}},
            {"type": "text", "text": "Now editing."},
        ]

    A turn typically receives several ``agent.message`` frames — one per
    assistant message in a multi-step interaction (text → tool_use →
    tool_result → text → tool_use → …). Each new message therefore
    *appends* to the turn rather than replacing previously finalised
    blocks. The previous implementation collapsed all text into a single
    block at position 0 and bunched tool_use blocks at the tail, which
    is what produced the "all text on top, all tools below" rendering
    bug.

    Streaming-delta interaction: ``agent.delta`` may have already created
    open (not-yet-finalised) text/thinking blocks. The first text item in
    this message finalises that open block; later text items in the same
    message become fresh appended blocks. Tool_use blocks are deduped
    against any already-present blocks by ``tool_use_id`` (preserves
    ``result_text`` from a prior ``agent.tool_result``).
    """
    content = frame.get("content")
    if not isinstance(content, list):
        return
    turn = _ensure_active_turn(state)

    existing_tools_by_id: dict[str, ToolUseBlock] = {
        b.tool_use_id: b for b in turn.blocks if isinstance(b, ToolUseBlock)
    }

    # The most recent NOT-yet-finalised text/thinking blocks belong to
    # this message — the streaming buffer the deltas were filling in.
    open_text = next(
        (b for b in reversed(turn.blocks) if isinstance(b, TextBlock) and not b.finalized),
        None,
    )
    open_thinking = next(
        (b for b in reversed(turn.blocks) if isinstance(b, ThinkingBlock) and not b.finalized),
        None,
    )

    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")

        if item_type == "text":
            text = str(item.get("text", "") or "")
            if open_text is not None:
                open_text.text = text
                open_text.finalized = True
                open_text = None  # only the first text item consumes it
            else:
                turn.blocks.append(TextBlock(text=text, finalized=True))

        elif item_type == "thinking":
            text = str(item.get("thinking") or item.get("text", "") or "")
            if open_thinking is not None:
                open_thinking.text = text
                open_thinking.finalized = True
                open_thinking = None
            else:
                turn.blocks.append(ThinkingBlock(text=text, finalized=True))

        elif item_type == "tool_use":
            tool_use_id = item.get("id") or item.get("tool_use_id")
            if not isinstance(tool_use_id, str) or not tool_use_id:
                continue
            name = str(item.get("name", "") or "")
            input_payload = item.get("input")
            if tool_use_id in existing_tools_by_id:
                existing = existing_tools_by_id[tool_use_id]
                if name:
                    existing.name = name
                if input_payload is not None:
                    existing.input = input_payload
            else:
                new_block = ToolUseBlock(
                    tool_use_id=tool_use_id,
                    name=name,
                    input=input_payload,
                )
                turn.blocks.append(new_block)
                existing_tools_by_id[tool_use_id] = new_block

    # Any open text/thinking block that this message didn't carry text
    # for still needs to be marked finalised so subsequent deltas open a
    # fresh block instead of appending into a stale buffer.
    if open_text is not None:
        open_text.finalized = True
    if open_thinking is not None:
        open_thinking.finalized = True


def _on_tool_use(state: SessionState, frame: dict[str, Any]) -> None:
    tool_use_id = frame.get("tool_use_id") or ""
    if not tool_use_id:
        return
    turn = _ensure_active_turn(state)
    # Preserve the input shape verbatim — the renderer adapts per-type.
    # Codex's exec_command sends a list (argv); Claude's tools send objects;
    # some backends send strings. Coercing to dict() crashes on lists.
    turn.blocks.append(
        ToolUseBlock(
            tool_use_id=tool_use_id,
            name=str(frame.get("name", "")),
            input=frame.get("input"),
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
            cache_creation_input_tokens=int(usage_raw.get("cache_creation_input_tokens", 0) or 0),
            cache_read_input_tokens=int(usage_raw.get("cache_read_input_tokens", 0) or 0),
            reasoning_output_tokens=int(usage_raw.get("reasoning_output_tokens", 0) or 0),
        )
        turn.usage = turn_usage
        state.cumulative_usage = state.cumulative_usage.merge(turn_usage)
    state.turn_active = False


# ---------------------------------------------------------------------------
# Handlers — per-session blemees-agentd.* (spec §15)
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


def _on_open_or_watch_ack(state: SessionState, frame: dict[str, Any]) -> None:
    """``agent.opened`` / ``agent.watching`` carry ``last_seq`` — the
    daemon's current high-water mark. If we're behind, start a replay
    progress window so the UI can render a loading overlay until we catch
    up. Brand-new sessions report ``last_seq:0`` (or absent) and skip.
    """
    last_seq = frame.get("last_seq")
    if not isinstance(last_seq, int) or last_seq <= state.last_seen_seq:
        # Caught up or nothing to replay — make sure we're not stuck in a
        # stale loading state from a prior reconnect.
        state.replay_target_seq = 0
        state.replay_start_seq = 0
        return
    state.replay_target_seq = last_seq
    state.replay_start_seq = state.last_seen_seq


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
    "agent.opened": _on_open_or_watch_ack,
    "agent.watching": _on_open_or_watch_ack,
    "agent.session_taken": _on_session_taken,
    "agent.session_closed": _on_session_closed,
    "agent.replay_gap": _on_replay_gap,
    "agent.session_info_reply": _on_session_info_reply,
    "agent.error": _on_error,
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

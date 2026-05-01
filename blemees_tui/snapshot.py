"""Per-session snapshot persistence — full ``SessionState`` (turns, blocks,
usage, draft) cached to ``snapshots/<session-id>.json`` so a TUI restart
can paint the cached transcript instantly and only replay frames the
daemon emitted since we last saved.

Separate from ``persistence.transcripts_dir`` (which holds the user's
Markdown ``Ctrl+S`` exports) — those are read-only deliverables, these
are the live cache.

Schema is versioned (``SNAPSHOT_SCHEMA_VERSION``). Future migrations land
here as branches keyed off the stored ``version`` field. Transient state
(``pending_errors``, ``replay_target_seq``, ``turn_active``, etc.) is
intentionally *not* persisted — those reset on restart and are rebuilt
from incoming frames.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .persistence import (
    SNAPSHOT_SCHEMA_VERSION,
    atomic_write_json,
    snapshot_path,
    snapshots_dir,
)
from .state import (
    ContentBlock,
    SessionMode,
    SessionState,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Turn,
    Usage,
)

logger = logging.getLogger("blemees_tui")


# ---------------------------------------------------------------------------
# Encode
# ---------------------------------------------------------------------------


def _block_to_dict(block: ContentBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text, "finalized": block.finalized}
    if isinstance(block, ThinkingBlock):
        return {"type": "thinking", "text": block.text, "finalized": block.finalized}
    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "tool_use_id": block.tool_use_id,
            "name": block.name,
            "input": block.input,
            "result_text": block.result_text,
            "is_error": block.is_error,
        }
    return {"type": "unknown"}


def _turn_to_dict(turn: Turn) -> dict[str, Any]:
    return {
        "user_text": turn.user_text,
        "blocks": [_block_to_dict(b) for b in turn.blocks],
        "duration_ms": turn.duration_ms,
        "usage": asdict(turn.usage),
        "result_subtype": turn.result_subtype,
        "error": turn.error,
        "locked": turn.locked,
    }


def session_to_dict(sess: SessionState) -> dict[str, Any]:
    """Serialise a ``SessionState`` to a JSON-safe dict.

    Skips transient fields that get rebuilt on next attach
    (``pending_errors``, ``pending_sends``, ``turn_active``, replay markers,
    crash/closed reasons, takeover ids).
    """
    return {
        "version": SNAPSHOT_SCHEMA_VERSION,
        "session_id": sess.session_id,
        "backend": sess.backend,
        "model": sess.model,
        "cwd": sess.cwd,
        "title": sess.title,
        "options": sess.options,
        "mode": sess.mode.value,
        "turns": [_turn_to_dict(t) for t in sess.turns],
        "cumulative_usage": asdict(sess.cumulative_usage),
        "context_tokens": sess.context_tokens,
        "context_window": sess.context_window,
        "last_seq": sess.last_seq,
        "last_seen_seq": sess.last_seen_seq,
        "last_active_at_ms": sess.last_active_at_ms,
        "started_at_ms": sess.started_at_ms,
        "owner_pid": sess.owner_pid,
        "draft": sess.draft,
    }


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------


def _block_from_dict(d: dict[str, Any]) -> ContentBlock | None:
    t = d.get("type")
    if t == "text":
        return TextBlock(
            text=str(d.get("text", "")),
            finalized=bool(d.get("finalized", False)),
        )
    if t == "thinking":
        return ThinkingBlock(
            text=str(d.get("text", "")),
            finalized=bool(d.get("finalized", False)),
        )
    if t == "tool_use":
        return ToolUseBlock(
            tool_use_id=str(d.get("tool_use_id", "")),
            name=str(d.get("name", "")),
            input=d.get("input"),
            result_text=d.get("result_text"),
            is_error=bool(d.get("is_error", False)),
        )
    return None


def _turn_from_dict(d: dict[str, Any]) -> Turn:
    blocks: list[ContentBlock] = []
    for raw in d.get("blocks") or []:
        if isinstance(raw, dict):
            block = _block_from_dict(raw)
            if block is not None:
                blocks.append(block)
    usage_raw = d.get("usage") or {}
    return Turn(
        user_text=str(d.get("user_text", "")),
        blocks=blocks,
        duration_ms=d.get("duration_ms"),
        usage=Usage(
            input_tokens=int(usage_raw.get("input_tokens", 0) or 0),
            output_tokens=int(usage_raw.get("output_tokens", 0) or 0),
            cache_creation_input_tokens=int(
                usage_raw.get("cache_creation_input_tokens", 0) or 0
            ),
            cache_read_input_tokens=int(
                usage_raw.get("cache_read_input_tokens", 0) or 0
            ),
            reasoning_output_tokens=int(usage_raw.get("reasoning_output_tokens", 0) or 0),
        ),
        result_subtype=d.get("result_subtype"),
        error=d.get("error") if isinstance(d.get("error"), dict) else None,
        locked=bool(d.get("locked", False)),
    )


def session_from_dict(d: dict[str, Any]) -> SessionState | None:
    sid = d.get("session_id")
    if not isinstance(sid, str) or not sid:
        return None
    cu_raw = d.get("cumulative_usage") or {}
    sess = SessionState(
        session_id=sid,
        backend=str(d.get("backend", "")),
        model=str(d.get("model", "")),
        cwd=str(d.get("cwd", "")),
        title=str(d.get("title", "")),
        options=dict(d.get("options") or {}),
        mode=_parse_mode(d.get("mode")),
        turns=[_turn_from_dict(t) for t in d.get("turns") or [] if isinstance(t, dict)],
        cumulative_usage=Usage(
            input_tokens=int(cu_raw.get("input_tokens", 0) or 0),
            output_tokens=int(cu_raw.get("output_tokens", 0) or 0),
            cache_creation_input_tokens=int(
                cu_raw.get("cache_creation_input_tokens", 0) or 0
            ),
            cache_read_input_tokens=int(cu_raw.get("cache_read_input_tokens", 0) or 0),
            reasoning_output_tokens=int(cu_raw.get("reasoning_output_tokens", 0) or 0),
        ),
        context_tokens=int(d.get("context_tokens", 0) or 0),
        context_window=int(d.get("context_window", 0) or 0),
        last_seq=int(d.get("last_seq", 0) or 0),
        last_seen_seq=int(d.get("last_seen_seq", 0) or 0),
        last_active_at_ms=int(d.get("last_active_at_ms", 0) or 0),
        started_at_ms=int(d.get("started_at_ms", 0) or 0),
        owner_pid=d.get("owner_pid") if isinstance(d.get("owner_pid"), int) else None,
        draft=str(d.get("draft", "") or ""),
    )
    return sess


def _parse_mode(raw: Any) -> SessionMode:
    try:
        return SessionMode(str(raw))
    except (ValueError, TypeError):
        return SessionMode.OWNED


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------


def save_snapshot(sess: SessionState) -> None:
    """Atomically write ``sess`` to its on-disk snapshot. Best-effort —
    failures log and continue (the cache is non-authoritative)."""
    try:
        atomic_write_json(snapshot_path(sess.session_id), session_to_dict(sess))
    except OSError:
        logger.exception("save_snapshot failed for %s", sess.session_id)


def load_snapshot(session_id: str) -> SessionState | None:
    """Return the cached ``SessionState`` for ``session_id``, or None if
    the snapshot is missing / unreadable / schema-mismatched."""
    p = snapshot_path(session_id)
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        logger.warning("load_snapshot: unreadable file %s", p)
        return None
    if not isinstance(raw, dict):
        return None
    if int(raw.get("version", 0) or 0) != SNAPSHOT_SCHEMA_VERSION:
        logger.info("load_snapshot: schema-version mismatch for %s, ignoring", session_id)
        return None
    return session_from_dict(raw)


def delete_snapshot(session_id: str) -> None:
    p = snapshot_path(session_id)
    try:
        p.unlink(missing_ok=True)
    except OSError:
        logger.exception("delete_snapshot failed for %s", session_id)


def list_snapshot_ids() -> list[str]:
    """Enumerate session ids whose snapshot files currently exist."""
    d = snapshots_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))

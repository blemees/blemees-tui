"""TUI-side persistence (spec §13).

Files kept under ``$XDG_STATE_HOME/blemees-tui/``:

* ``sessions.json``   — live + watching sessions index, rewritten on every change.
* ``history.json``    — bounded ring (200 entries) of closed-but-remembered
                        sessions.
* ``snapshots/``      — full per-session in-memory state cached to disk
                        (turn list, blocks, usage, drafts) so a TUI restart
                        skips the full daemon replay.
* ``blemees-tui.log`` — rotating log (weekly, 7 keep). Configured by the
                        connection layer; this module hands back the path.
* ``transcripts/``    — ``Ctrl+S`` Markdown exports (different from the
                        snapshots cache above — those are JSON state).

JSON writes are atomic: write to ``<file>.tmp``, ``fsync``, ``rename``.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SESSIONS_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1
HISTORY_MAX_ENTRIES = 200


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "blemees-tui"


def sessions_path() -> Path:
    return state_dir() / "sessions.json"


def history_path() -> Path:
    return state_dir() / "history.json"


def log_path() -> Path:
    return state_dir() / "blemees-tui.log"


def transcripts_dir() -> Path:
    return state_dir() / "transcripts"


def snapshots_dir() -> Path:
    return state_dir() / "snapshots"


def snapshot_path(session_id: str) -> Path:
    return snapshots_dir() / f"{session_id}.json"


def ensure_state_dir() -> Path:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# sessions.json
# ---------------------------------------------------------------------------


@dataclass
class StoredSession:
    """One row of ``sessions.json`` (spec §7.5)."""

    session_id: str
    backend: str
    model: str
    cwd: str
    title: str
    options: dict[str, Any]
    last_seen_seq: int
    last_active_at_ms: int
    mode: str  # "owned" | "watching" — closed sessions live in history.json
    marked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "backend": self.backend,
            "model": self.model,
            "cwd": self.cwd,
            "title": self.title,
            "options": self.options,
            "last_seen_seq": self.last_seen_seq,
            "last_active_at_ms": self.last_active_at_ms,
            "mode": self.mode,
            "marked": self.marked,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StoredSession:
        return cls(
            session_id=str(raw["session_id"]),
            backend=str(raw.get("backend", "")),
            model=str(raw.get("model", "")),
            cwd=str(raw.get("cwd", "")),
            title=str(raw.get("title", "")),
            options=dict(raw.get("options") or {}),
            last_seen_seq=int(raw.get("last_seen_seq", 0)),
            last_active_at_ms=int(raw.get("last_active_at_ms", 0)),
            mode=str(raw.get("mode", "owned")),
            marked=bool(raw.get("marked", False)),
        )


def load_sessions(path: Path | None = None) -> list[StoredSession]:
    p = path or sessions_path()
    if not p.exists():
        return []
    try:
        with p.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    rows = raw.get("sessions") or []
    out: list[StoredSession] = []
    for row in rows:
        if isinstance(row, dict) and "session_id" in row:
            try:
                out.append(StoredSession.from_dict(row))
            except (KeyError, ValueError, TypeError):
                continue
    return out


def save_sessions(sessions: list[StoredSession], path: Path | None = None) -> None:
    p = path or sessions_path()
    payload = {
        "version": SESSIONS_SCHEMA_VERSION,
        "sessions": [s.to_dict() for s in sessions],
    }
    atomic_write_json(p, payload)


# ---------------------------------------------------------------------------
# history.json
# ---------------------------------------------------------------------------


@dataclass
class HistoryEntry:
    session_id: str
    backend: str
    title: str
    cwd: str
    closed_at_ms: int
    reason: str  # "user_closed" | "owner_closed" | "deleted" | "session_unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "backend": self.backend,
            "title": self.title,
            "cwd": self.cwd,
            "closed_at_ms": self.closed_at_ms,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> HistoryEntry:
        return cls(
            session_id=str(raw["session_id"]),
            backend=str(raw.get("backend", "")),
            title=str(raw.get("title", "")),
            cwd=str(raw.get("cwd", "")),
            closed_at_ms=int(raw.get("closed_at_ms", 0)),
            reason=str(raw.get("reason", "")),
        )


def load_history(path: Path | None = None) -> list[HistoryEntry]:
    p = path or history_path()
    if not p.exists():
        return []
    try:
        with p.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    rows = raw.get("history") or []
    out: list[HistoryEntry] = []
    for row in rows:
        if isinstance(row, dict) and "session_id" in row:
            try:
                out.append(HistoryEntry.from_dict(row))
            except (KeyError, ValueError, TypeError):
                continue
    return out


def save_history(entries: list[HistoryEntry], path: Path | None = None) -> None:
    p = path or history_path()
    bounded = entries[-HISTORY_MAX_ENTRIES:]
    payload = {"version": SESSIONS_SCHEMA_VERSION, "history": [e.to_dict() for e in bounded]}
    atomic_write_json(p, payload)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def configure_logger(level: str = "info", keep_days: int = 7) -> logging.Logger:
    """Configure the rotating ``blemees-tui.log`` handler.

    Idempotent — repeated calls re-use the existing handler. Returns the
    package logger.
    """
    ensure_state_dir()
    logger = logging.getLogger("blemees_tui")
    logger.setLevel(_LEVELS.get(level.lower(), logging.INFO))
    logger.propagate = False

    expected = log_path()
    have_handler = any(
        isinstance(h, logging.handlers.TimedRotatingFileHandler)
        and Path(h.baseFilename) == expected
        for h in logger.handlers
    )
    if have_handler:
        return logger

    handler = logging.handlers.TimedRotatingFileHandler(
        expected, when="W0", backupCount=keep_days, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    logger.addHandler(handler)
    return logger


# ---------------------------------------------------------------------------
# Transcript export
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def slugify(title: str) -> str:
    s = _SLUG_RE.sub("-", title).strip("-").lower()
    return s or "session"


def transcript_filename(title: str, session_id: str) -> str:
    short = session_id.split("-", 1)[0][:8] if session_id else "noid"
    return f"{slugify(title)[:60]}-{short}.md"

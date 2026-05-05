"""In-memory state model for the TUI (spec §4).

Pure dataclasses — the reducer (``reducer.py``) consumes these plus a wire
frame and returns an updated ``SessionState``. No I/O here.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SessionMode(StrEnum):
    OWNED = "owned"
    WATCHING = "watching"
    DETACHED = "detached"
    CRASHED = "crashed"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Content-block primitives
# ---------------------------------------------------------------------------


@dataclass
class TextBlock:
    text: str = ""
    finalized: bool = False  # set true by agent.message


@dataclass
class ThinkingBlock:
    text: str = ""
    finalized: bool = False


@dataclass
class ToolUseBlock:
    tool_use_id: str
    name: str
    # Backend-dependent — Claude's tools use objects, but Codex's
    # ``exec_command_begin`` sends the argv as a list, and other tools may
    # send a string. Renderer formats per-shape.
    input: Any
    result_text: str | None = None  # populated by agent.tool_result
    is_error: bool = False


ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    reasoning_output_tokens: int = 0  # codex-only; 0 elsewhere

    def merge(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens
            + other.cache_creation_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            reasoning_output_tokens=self.reasoning_output_tokens + other.reasoning_output_tokens,
        )


@dataclass
class Turn:
    user_text: str = ""
    blocks: list[ContentBlock] = field(default_factory=list)
    duration_ms: int | None = None
    usage: Usage = field(default_factory=Usage)
    result_subtype: str | None = None  # success | interrupted | error
    error: dict[str, Any] | None = None
    locked: bool = False  # True after agent.result


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


@dataclass
class SessionState:
    session_id: str
    backend: str = ""
    model: str = ""
    cwd: str = ""
    title: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    mode: SessionMode = SessionMode.OWNED

    turns: list[Turn] = field(default_factory=list)
    cumulative_usage: Usage = field(default_factory=Usage)
    context_tokens: int = 0
    context_window: int = 0

    last_seq: int = 0
    last_seen_seq: int = 0
    last_active_at_ms: int = 0
    started_at_ms: int = 0
    owner_pid: int | None = None

    turn_active: bool = False
    crashed_reason: str | None = None
    closed_reason: str | None = None
    taken_by_pid: int | None = None
    replay_gap: bool = False

    pending_errors: list[dict[str, Any]] = field(default_factory=list)
    # Messages typed while the agent was busy. Flushed FIFO when
    # ``agent.result`` lands and ``turn_active`` flips back to False.
    pending_sends: list[str] = field(default_factory=list)
    # Unsubmitted composer text, snapshotted on session-switch so each
    # session keeps its own in-progress message.
    draft: str = ""
    # Highest ``seq`` we expect to see during replay, set from
    # ``blemeesd.opened.last_seq`` / ``blemeesd.watching.last_seq``. While
    # ``last_seen_seq < replay_target_seq`` the chat pane shows a loading
    # overlay; cleared back to 0 once we catch up.
    replay_target_seq: int = 0
    # The seq we started replay from — needed to render a "X of Y"
    # progress that doesn't look weird on warm reconnects (where we start
    # 95% of the way through).
    replay_start_seq: int = 0
    # Marked for broadcast — a ``>> message`` typed in the composer fans
    # out to every session with this flag set. Persisted across restarts.
    marked: bool = False


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------


class EventLogSource(StrEnum):
    DAEMON_ERROR = "daemon-error"
    DAEMON_STDERR = "daemon-stderr"
    NOTICE = "notice"
    TUI_INTERNAL = "tui-internal"
    CONNECTION = "connection"


@dataclass
class EventLogEntry:
    ts_ms: int
    source: EventLogSource
    session_id: str | None
    category: str  # short tag — e.g. "rate_limits", "auth_failed", "hello"
    message: str
    context: dict[str, Any] = field(default_factory=dict)


class EventLog:
    """Bounded ring buffer of recent log entries (spec §14.2 — 2000 entries)."""

    def __init__(self, capacity: int = 2000) -> None:
        self._buf: deque[EventLogEntry] = deque(maxlen=capacity)

    def append(
        self,
        source: EventLogSource,
        category: str,
        message: str,
        *,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> EventLogEntry:
        entry = EventLogEntry(
            ts_ms=int(time.time() * 1000),
            source=source,
            session_id=session_id,
            category=category,
            message=message,
            context=context or {},
        )
        self._buf.append(entry)
        return entry

    def __iter__(self):
        return iter(self._buf)

    def __len__(self) -> int:
        return len(self._buf)

    def snapshot(self) -> list[EventLogEntry]:
        return list(self._buf)


# ---------------------------------------------------------------------------
# Top-level app state
# ---------------------------------------------------------------------------


@dataclass
class DaemonInfo:
    """Mirror of ``blemeesd.hello_ack`` — populated after handshake."""

    daemon: str = ""  # e.g. "blemeesd/0.9.2"
    protocol: str = ""
    pid: int = 0
    backends: dict[str, str] = field(default_factory=dict)


@dataclass
class RateLimitsNotice:
    """Most-recent ``agent.notice{category:rate_limits}`` payload (§9.6)."""

    level: str = "info"  # "info" | "warn"
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    received_at_ms: int = 0


@dataclass
class HistoryRecord:
    """In-memory history entry — mirror of persistence.HistoryEntry (§7.4)."""

    session_id: str
    backend: str
    title: str
    cwd: str
    closed_at_ms: int
    reason: str  # "user_closed" | "deleted" | "owner_closed" | "session_unknown"


@dataclass
class AppState:
    daemon: DaemonInfo = field(default_factory=DaemonInfo)
    sessions: dict[str, SessionState] = field(default_factory=dict)
    history: list[HistoryRecord] = field(default_factory=list)
    active_session_id: str | None = None
    event_log: EventLog = field(default_factory=EventLog)
    connection_status: str = "disconnected"  # connected | reconnecting | disconnected
    reconnect_attempt: int = 0
    rate_limits: RateLimitsNotice | None = None

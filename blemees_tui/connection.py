"""Connection layer (spec §6).

A direct ``asyncio`` implementation of the ``blemees/2`` wire protocol
(rather than a thin wrap around ``blemees.client.BlemeesClient``) — the TUI
needs control over multi-session multiplexing, watch/unwatch frames, and the
``list_sessions{live:true}`` filter that the reference client doesn't expose
at the high level.

Responsibilities:

* One Unix-socket connection at a time.
* Per-``session_id`` inbox queues + a watcher inbox.
* Reconnect with backoff (1s → 30s, ×1.5, ±20%, indefinite).
* Liveness ``ping`` every 15s while idle.
* Central event-log feed for the TUI's observability surfaces.

Frame dispatch follows the reducer's contract: this module forwards every
``agent.*`` and per-session ``blemees-agentd.*`` frame to the active reducer pump
via the ``on_frame`` callback the app registers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import CLIENT_NAME, PROTOCOL_VERSION

logger = logging.getLogger("blemees_tui")


# ---------------------------------------------------------------------------
# Socket resolution (mirrors blemees.client.default_socket_path)
# ---------------------------------------------------------------------------


def default_socket_path() -> str:
    env = os.environ.get("BLEMEES_AGENTD_SOCKET")
    if env:
        return env
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return str(Path(xdg) / "blemees" / "agentd.sock")
    return f"/tmp/blemees-agentd-{os.getuid()}.sock"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConnectionError_(RuntimeError):
    """Raised when a frame indicates the connection is unusable."""


class FatalProtocolError(RuntimeError):
    """``protocol_mismatch`` from the daemon — caller should exit."""


# ---------------------------------------------------------------------------
# Frame routing model
# ---------------------------------------------------------------------------


FrameHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass
class _PendingRequest:
    fut: asyncio.Future
    types: tuple[str, ...]


@dataclass
class _BackoffPolicy:
    initial: float = 1.0
    cap: float = 30.0
    factor: float = 1.5
    jitter: float = 0.2  # ±20%

    def delay(self, attempt: int) -> float:
        base = min(self.cap, self.initial * (self.factor ** max(0, attempt - 1)))
        spread = base * self.jitter
        return max(0.1, base + random.uniform(-spread, spread))


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


@dataclass
class ConnectionStatus:
    state: str = "disconnected"  # connected | reconnecting | disconnected | fatal
    attempt: int = 0
    next_in_ms: int = 0
    last_error: str = ""


class Connection:
    """``blemees-agentd`` socket multiplexer for the TUI."""

    def __init__(
        self,
        *,
        socket_path: str | None = None,
        on_frame: FrameHandler | None = None,
        on_status_change: Callable[[ConnectionStatus], None] | None = None,
        ping_interval: float = 15.0,
        idle_threshold: float = 10.0,
    ) -> None:
        self.socket_path = socket_path or default_socket_path()
        self._on_frame = on_frame
        self._on_status_change = on_status_change
        self._ping_interval = ping_interval
        self._idle_threshold = idle_threshold

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._stop = asyncio.Event()

        self._next_req = 0
        self._pending: dict[str, _PendingRequest] = {}
        self._last_inbound_at = time.monotonic()

        self.status = ConnectionStatus()
        self.daemon_info: dict[str, Any] = {}

        # Sessions the TUI considers live — restored across reconnects.
        # Each entry: {"session_id", "kind": "owned"|"watching", "backend",
        #              "options", "last_seen_seq"}.
        self._tracked: dict[str, dict[str, Any]] = {}

        self._policy = _BackoffPolicy()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Run the connection supervisor in the background."""
        if self._supervisor_task is not None:
            return
        self._stop.clear()
        self._supervisor_task = asyncio.create_task(self._supervise(), name="blemees-conn")

    async def stop(self) -> None:
        self._stop.set()
        for task in (self._supervisor_task, self._reader_task, self._ping_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        self._supervisor_task = None
        self._reader_task = None
        self._ping_task = None
        await self._teardown_writer()

    # ------------------------------------------------------------------
    # Tracked-session bookkeeping (used on reconnect)
    # ------------------------------------------------------------------

    def track_owned(
        self,
        session_id: str,
        *,
        backend: str,
        options: dict[str, Any] | None = None,
        last_seen_seq: int = 0,
    ) -> None:
        self._tracked[session_id] = {
            "kind": "owned",
            "session_id": session_id,
            "backend": backend,
            "options": dict(options or {}),
            "last_seen_seq": int(last_seen_seq),
        }

    def track_watch(self, session_id: str, *, last_seen_seq: int = 0) -> None:
        self._tracked[session_id] = {
            "kind": "watching",
            "session_id": session_id,
            "last_seen_seq": int(last_seen_seq),
        }

    def untrack(self, session_id: str) -> None:
        self._tracked.pop(session_id, None)

    def update_last_seen(self, session_id: str, seq: int) -> None:
        if session_id in self._tracked:
            self._tracked[session_id]["last_seen_seq"] = max(
                self._tracked[session_id].get("last_seen_seq", 0), int(seq)
            )

    # ------------------------------------------------------------------
    # Verbs (spec §16, §7)
    # ------------------------------------------------------------------

    async def open_session(
        self,
        session_id: str,
        *,
        backend: str,
        options: dict[str, Any] | None = None,
        resume: bool = False,
        last_seen_seq: int | None = None,
    ) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "type": "blemeesd.open",
            "session_id": session_id,
            "backend": backend,
            "options": {backend: dict(options or {})},
        }
        if resume:
            frame["resume"] = True
        if last_seen_seq is not None:
            frame["last_seen_seq"] = int(last_seen_seq)
        return await self._request(frame, ack_types=("blemeesd.opened",))

    async def watch_session(
        self,
        session_id: str,
        *,
        last_seen_seq: int = 0,
    ) -> dict[str, Any]:
        frame = {
            "type": "blemeesd.watch",
            "session_id": session_id,
            "last_seen_seq": int(last_seen_seq),
        }
        return await self._request(frame, ack_types=("blemeesd.watching",))

    async def unwatch(self, session_id: str) -> dict[str, Any]:
        return await self._request(
            {"type": "blemeesd.unwatch", "session_id": session_id},
            ack_types=("blemeesd.unwatched",),
        )

    async def close_session(self, session_id: str, *, delete: bool = False) -> dict[str, Any]:
        return await self._request(
            {"type": "blemeesd.close", "session_id": session_id, "delete": bool(delete)},
            ack_types=("blemeesd.closed",),
        )

    async def interrupt(self, session_id: str) -> None:
        await self._send({"type": "blemeesd.interrupt", "session_id": session_id})

    async def send_user(self, session_id: str, text: str) -> None:
        await self._send(
            {
                "type": "agent.user",
                "session_id": session_id,
                "message": {"role": "user", "content": text},
            }
        )

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        live: bool | None = None,
    ) -> list[dict[str, Any]]:
        frame: dict[str, Any] = {"type": "blemeesd.list_sessions"}
        if cwd is not None:
            frame["cwd"] = cwd
        if live is not None:
            frame["live"] = bool(live)
        reply = await self._request(frame, ack_types=("blemeesd.sessions",))
        sessions = reply.get("sessions") or []
        return list(sessions) if isinstance(sessions, list) else []

    async def session_info(self, session_id: str) -> dict[str, Any]:
        return await self._request(
            {"type": "blemeesd.session_info", "session_id": session_id},
            ack_types=("blemeesd.session_info_reply",),
        )

    # ------------------------------------------------------------------
    # Internal: request/response with id correlation
    # ------------------------------------------------------------------

    async def _request(self, frame: dict[str, Any], *, ack_types: tuple[str, ...]) -> dict[str, Any]:
        self._next_req += 1
        req_id = f"req_{self._next_req}"
        frame = {**frame, "id": req_id}
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = _PendingRequest(fut=fut, types=ack_types)
        try:
            await self._send(frame)
            reply = await fut
        finally:
            self._pending.pop(req_id, None)
        if reply.get("type") == "blemeesd.error":
            raise ConnectionError_(f"{reply.get('code', '')}: {reply.get('message', '')}")
        return reply

    async def _send(self, frame: dict[str, Any]) -> None:
        if self._writer is None:
            raise ConnectionError_("not connected")
        data = (json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._write_lock:
            self._writer.write(data)
            await self._writer.drain()

    # ------------------------------------------------------------------
    # Supervisor: connect / reconnect loop
    # ------------------------------------------------------------------

    async def _supervise(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                self._set_status("reconnecting" if attempt > 0 else "disconnected", attempt=attempt)
                await self._connect_once()
                attempt = 0
                self._set_status("connected", attempt=0)
                await self._restore_tracked()
                # Reader runs until socket dies; await it to know when to retry.
                if self._reader_task is not None:
                    await self._reader_task
            except FatalProtocolError as exc:
                self._set_status("fatal", error=str(exc))
                logger.error("fatal protocol error: %s", exc)
                self._stop.set()
                return
            except (OSError, ConnectionError_, asyncio.IncompleteReadError) as exc:
                self._set_status("reconnecting", attempt=attempt + 1, error=str(exc))
                logger.warning("connection error: %s", exc)
            finally:
                await self._teardown_writer()

            if self._stop.is_set():
                break
            attempt += 1
            delay = self._policy.delay(attempt)
            self.status.next_in_ms = int(delay * 1000)
            self._notify_status()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                # Stop was set during wait — exit.
                break
            except TimeoutError:
                continue

    async def _connect_once(self) -> None:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        self._reader = reader
        self._writer = writer
        await self._send(
            {
                "type": "blemeesd.hello",
                "client": CLIENT_NAME,
                "protocol": PROTOCOL_VERSION,
            }
        )
        ack = await self._read_one()
        if ack.get("type") != "blemeesd.hello_ack":
            if ack.get("code") == "protocol_mismatch":
                raise FatalProtocolError(ack.get("message", "protocol mismatch"))
            raise ConnectionError_(f"unexpected hello ack: {ack!r}")
        self.daemon_info = ack
        # Forward the hello_ack to the app handler too — it lives outside the
        # reader loop because we read it inline above, but the app needs it
        # to populate state.daemon (footer, new-session backend list, …).
        await self._dispatch(ack)
        self._reader_task = asyncio.create_task(self._reader_loop(), name="blemees-reader")
        self._ping_task = asyncio.create_task(self._ping_loop(), name="blemees-ping")
        self._last_inbound_at = time.monotonic()

    async def _restore_tracked(self) -> None:
        """Re-issue ``open … resume:true`` / ``watch`` for tracked sessions."""
        for entry in list(self._tracked.values()):
            try:
                if entry["kind"] == "owned":
                    await self.open_session(
                        entry["session_id"],
                        backend=entry.get("backend", ""),
                        options=entry.get("options") or {},
                        resume=True,
                        last_seen_seq=entry.get("last_seen_seq", 0),
                    )
                else:
                    await self.watch_session(
                        entry["session_id"],
                        last_seen_seq=entry.get("last_seen_seq", 0),
                    )
            except ConnectionError_ as exc:
                # session_unknown → caller will see this via the on_frame
                # error stream too; we drop it from tracked here.
                logger.info("dropping tracked session %s on restore: %s", entry["session_id"], exc)
                self._tracked.pop(entry["session_id"], None)

    async def _teardown_writer(self) -> None:
        if self._ping_task is not None:
            self._ping_task.cancel()
            with contextlib.suppress(BaseException):
                await self._ping_task
            self._ping_task = None
        for pending in self._pending.values():
            if not pending.fut.done():
                pending.fut.set_exception(ConnectionError_("connection lost"))
        self._pending.clear()
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except (OSError, ConnectionError):
                pass
            self._writer = None
        self._reader = None

    # ------------------------------------------------------------------
    # Reader / ping
    # ------------------------------------------------------------------

    async def _read_one(self) -> dict[str, Any]:
        assert self._reader is not None
        raw = await self._reader.readuntil(b"\n")
        return json.loads(raw.rstrip(b"\r\n").decode("utf-8"))

    async def _reader_loop(self) -> None:
        try:
            while True:
                frame = await self._read_one()
                self._last_inbound_at = time.monotonic()
                await self._dispatch(frame)
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            logger.info("reader loop ended: %s", exc)

    async def _dispatch(self, frame: dict[str, Any]) -> None:
        ftype = frame.get("type", "")
        # 1. Resolve any pending request via id correlation.
        req_id = frame.get("id")
        if isinstance(req_id, str) and req_id in self._pending:
            pending = self._pending[req_id]
            if ftype in pending.types or ftype == "blemeesd.error":
                if not pending.fut.done():
                    pending.fut.set_result(frame)
        # 2. Track seq for tracked sessions.
        sid = frame.get("session_id")
        seq = frame.get("seq")
        if isinstance(sid, str) and isinstance(seq, int):
            self.update_last_seen(sid, seq)
        # 3. Forward unsolicited frames upstream.
        if self._on_frame is not None and ftype:
            try:
                result = self._on_frame(frame)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("on_frame handler failed for %s", ftype)

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(self._ping_interval)
            if self._writer is None:
                return
            idle = time.monotonic() - self._last_inbound_at
            if idle < self._idle_threshold:
                continue
            try:
                await self._send({"type": "blemeesd.ping"})
            except (OSError, ConnectionError_):
                return

    # ------------------------------------------------------------------
    # Status notifications
    # ------------------------------------------------------------------

    def _set_status(
        self,
        state: str,
        *,
        attempt: int | None = None,
        error: str | None = None,
    ) -> None:
        self.status.state = state
        if attempt is not None:
            self.status.attempt = attempt
        if state == "connected":
            self.status.next_in_ms = 0
            self.status.last_error = ""
        if error is not None:
            self.status.last_error = error
        self._notify_status()

    def _notify_status(self) -> None:
        if self._on_status_change is not None:
            try:
                self._on_status_change(self.status)
            except Exception:
                logger.exception("on_status_change handler failed")

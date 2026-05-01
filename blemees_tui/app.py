"""Textual App for blemees-tui (spec §11).

This is the v0.1 scaffold. The pure layers (config, state, reducer,
persistence, connection) are wired in; rendering / interaction depth grows
in follow-up work as the widgets fill out.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections import deque
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical

from . import __version__
from .commands import is_uuid as is_command_uuid, parse as parse_command
from .config import Config, apply_cli_overrides, load_config
from .connection import Connection, ConnectionStatus
from .persistence import (
    HistoryEntry,
    StoredSession,
    configure_logger,
    load_history,
    load_sessions,
    save_history,
    save_sessions,
)
from .reducer import apply as reduce_frame
from .state import (
    AppState,
    DaemonInfo,
    EventLogSource,
    HistoryRecord,
    RateLimitsNotice,
    SessionMode,
    SessionState,
)
from .widgets import (
    ChatPaneWidget,
    CompletionPopup,
    ComposerWidget,
    ConnectionBanner,
    DebugPane,
    EventLogOverlay,
    FooterStatusWidget,
    SidebarWidget,
)
from .widgets.modals import AttachModal, HelpModal, NewSessionModal


class BlemeesTuiApp(App):
    """Top-level Textual application."""

    TITLE = "blemees"
    SUB_TITLE = f"v{__version__}"

    CSS = """
    #stack { height: 1fr; width: 100%; }
    #main { height: 1fr; width: 100%; }
    #main > #sidebar { width: 28; height: 100%; }
    #main > #chat { width: 1fr; height: 100%; }
    #composer { width: 100%; height: auto; }
    """

    BINDINGS = [
        Binding("ctrl+n", "new_session", "New"),
        Binding("ctrl+t", "attach", "Attach"),
        Binding("ctrl+w", "close_session", "Close"),
        Binding("ctrl+shift+w", "delete_session", "Delete"),
        Binding("ctrl+c", "interrupt", "Interrupt"),
        Binding("ctrl+r", "reconnect", "Reconnect"),
        Binding("ctrl+e", "event_log", "Event log"),
        Binding("ctrl+d", "debug_pane", "Debug"),
        Binding("ctrl+s", "save_transcript", "Save transcript"),
        Binding("ctrl+tab", "next_session", "Next session", show=False),
        Binding("ctrl+shift+tab", "prev_session", "Prev session", show=False),
        *[Binding(str(i), f"select_session({i})", f"Session {i}", show=False) for i in range(1, 10)],
        Binding("t", "toggle_thinking", "Toggle thinking", show=False),
        Binding("colon", "focus_composer_command", "Command", show=False),
        # Chat scroll — priority so they fire even when the composer's
        # TextArea has focus (TextArea claims pageup/pagedown/home/end by
        # default for in-buffer cursor movement; we override since the
        # composer is short and chat scroll is the more useful binding).
        Binding("pageup", "scroll_up_page", show=False, priority=True),
        Binding("pagedown", "scroll_down_page", show=False, priority=True),
        Binding("ctrl+up", "scroll_up_line", show=False, priority=True),
        Binding("ctrl+down", "scroll_down_line", show=False, priority=True),
        Binding("home", "scroll_top", show=False, priority=True),
        Binding("end", "scroll_bottom", show=False, priority=True),
        Binding("ctrl+home", "scroll_top", show=False, priority=True),
        Binding("ctrl+end", "scroll_bottom", show=False, priority=True),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        socket_override: str | None = None,
        config_path_override: str | None = None,
        log_level_override: str | None = None,
    ) -> None:
        super().__init__()
        self._config_path = config_path_override
        self.config_obj: Config = apply_cli_overrides(
            load_config(),
            socket=socket_override,
            log_level=log_level_override,
        )
        configure_logger(level=self.config_obj.logging.level, keep_days=self.config_obj.logging.keep_days)

        self.state = AppState()
        self._connection: Connection | None = None
        self._debug_frames: deque[tuple[str, dict[str, Any]]] = deque(maxlen=DebugPane.CAPACITY)
        self._socket_override = socket_override
        self._show_thinking: bool = self.config_obj.ui.show_thinking

    # ------------------------------------------------------------------
    # Compose / mount
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield ConnectionBanner(id="conn-banner")
        with Vertical(id="stack"):
            yield Horizontal(
                SidebarWidget(self.state, id="sidebar"),
                ChatPaneWidget(id="chat"),
                id="main",
            )
            yield CompletionPopup(id="completion")
            yield ComposerWidget(id="composer")
        yield FooterStatusWidget(self.state, id="footer")

    async def on_mount(self) -> None:
        # Restore history (read-only on mount; mutated by close/delete).
        for entry in load_history():
            self.state.history.append(
                HistoryRecord(
                    session_id=entry.session_id,
                    backend=entry.backend,
                    title=entry.title,
                    cwd=entry.cwd,
                    closed_at_ms=entry.closed_at_ms,
                    reason=entry.reason,
                )
            )

        # Restore tracked sessions from disk before starting the connection
        # so the supervisor reissues `open … resume:true` for them.
        for stored in load_sessions():
            sess = SessionState(
                session_id=stored.session_id,
                backend=stored.backend,
                model=stored.model,
                cwd=stored.cwd,
                title=stored.title,
                options=stored.options,
                last_seen_seq=stored.last_seen_seq,
                last_active_at_ms=stored.last_active_at_ms,
                mode=SessionMode(stored.mode) if stored.mode else SessionMode.OWNED,
            )
            self.state.sessions[sess.session_id] = sess

        self._connection = Connection(
            socket_path=self.config_obj.connection.socket or self._socket_override or None,
            on_frame=self._handle_frame,
            on_status_change=self._on_connection_status,
        )
        # Re-track each restored session so the supervisor will resume it.
        # First attach uses last_seen_seq=0 so the daemon replays the full
        # event log and the reducer rebuilds in-memory turns from scratch.
        # Subsequent reconnects within this TUI run use the up-to-date
        # last_seen_seq carried on _tracked (Connection.update_last_seen
        # bumps it as frames arrive), so we don't pay the full-replay cost
        # again unless the user restarts the TUI.
        for sess in self.state.sessions.values():
            if sess.mode == SessionMode.WATCHING:
                self._connection.track_watch(sess.session_id, last_seen_seq=0)
            else:
                self._connection.track_owned(
                    sess.session_id,
                    backend=sess.backend,
                    options=sess.options,
                    last_seen_seq=0,
                )

        await self._connection.start()
        self._refresh_ui()

    async def on_unmount(self) -> None:
        if self._connection is not None:
            await self._connection.stop()
        self._persist_sessions()

    # ------------------------------------------------------------------
    # Connection callbacks
    # ------------------------------------------------------------------

    def _handle_frame(self, frame: dict[str, Any]) -> None:
        self._debug_frames.append(("in", frame))
        ftype = frame.get("type", "")
        if ftype == "blemeesd.hello_ack":
            self.state.daemon = DaemonInfo(
                daemon=str(frame.get("daemon", "")),
                protocol=str(frame.get("protocol", "")),
                pid=int(frame.get("pid", 0) or 0),
                backends=dict(frame.get("backends") or {}),
            )
            self.state.event_log.append(
                EventLogSource.CONNECTION, "hello", f"connected to {self.state.daemon.daemon}"
            )
        elif ftype == "blemeesd.error" and "session_id" not in frame:
            # Connection-scope error — log; reducer doesn't see it.
            code = str(frame.get("code", ""))
            msg = str(frame.get("message", ""))
            self.state.event_log.append(EventLogSource.DAEMON_ERROR, code, msg)
            if code in {"slow_consumer", "oversize_message", "daemon_shutdown"}:
                self._latch_connection_fatal(code, msg)

        sid = frame.get("session_id")
        if isinstance(sid, str):
            sess = self.state.sessions.get(sid)
            if sess is None and ftype.startswith(("agent.", "blemeesd.")):
                # Unknown session — only register if it's a session-scoped frame
                # we can reasonably attach (e.g. agent.system_init from a watch).
                sess = SessionState(session_id=sid)
                self.state.sessions[sid] = sess
            if sess is not None:
                reduce_frame(sess, frame)
                if ftype == "agent.notice":
                    self._on_notice(sid, frame)
                if ftype == "agent.result":
                    # Refresh context_tokens / cumulative usage for the footer
                    # — fire-and-forget; reply handled via the reducer.
                    self._schedule_session_info(sid)
                    if sess is not None and sess.pending_sends:
                        # Flush any locally-queued user messages.
                        asyncio.create_task(self._flush_pending_sends(sid))
                if ftype == "blemeesd.session_closed":
                    self._archive_to_history(sess, reason=str(frame.get("reason", "owner_closed")))
                    self._connection and self._connection.untrack(sid)
                    self.state.sessions.pop(sid, None)
                    if self.state.active_session_id == sid:
                        self.state.active_session_id = None
                    self._persist_sessions()
                if ftype == "blemeesd.error" and frame.get("code") == "session_unknown":
                    self._archive_to_history(sess, reason="session_unknown")
                    self._connection and self._connection.untrack(sid)
                    self.state.sessions.pop(sid, None)
                    if self.state.active_session_id == sid:
                        self.state.active_session_id = None
                    self._persist_sessions()

        self._refresh_ui()

    def _on_notice(self, session_id: str, frame: dict[str, Any]) -> None:
        category = str(frame.get("category", ""))
        level = str(frame.get("level", "info"))
        text = str(frame.get("text", ""))
        data = frame.get("data") if isinstance(frame.get("data"), dict) else {}
        self.state.event_log.append(
            EventLogSource.NOTICE, category, text, session_id=session_id
        )
        if category == "rate_limits":
            self.state.rate_limits = RateLimitsNotice(
                level=level,
                text=text,
                data=dict(data or {}),
                session_id=session_id,
                received_at_ms=int(time.time() * 1000),
            )

    def _schedule_session_info(self, session_id: str) -> None:
        if self._connection is None:
            return

        async def _ask() -> None:
            try:
                assert self._connection is not None
                await self._connection.session_info(session_id)
            except Exception:
                # Connection layer logs; nothing actionable here.
                return

        asyncio.create_task(_ask())

    def _latch_connection_fatal(self, code: str, message: str) -> None:
        text = {
            "slow_consumer": (
                f"slow_consumer — the TUI fell behind reading the socket "
                f"and the daemon dropped us. Reconnecting… ({message})"
            ),
            "oversize_message": (
                f"oversize_message — a frame exceeded the daemon's per-message limit ({message})"
            ),
            "daemon_shutdown": (
                f"daemon_shutdown — the daemon is going away. Reconnect when it's back. ({message})"
            ),
        }.get(code, f"{code}: {message}")
        try:
            banner = self.query_one("#conn-banner", ConnectionBanner)
            banner.set_fatal(text)
        except Exception:
            pass

    def _on_connection_status(self, status: ConnectionStatus) -> None:
        self.state.connection_status = status.state
        self.state.reconnect_attempt = status.attempt
        self._refresh_footer()
        try:
            banner = self.query_one("#conn-banner", ConnectionBanner)
            if status.state == "connected":
                banner.clear_fatal()
            banner.set_connection(
                state=status.state,
                attempt=status.attempt,
                next_in_ms=status.next_in_ms,
                last_error=status.last_error,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _refresh_ui(self) -> None:
        self._refresh_footer()
        active = (
            self.state.sessions.get(self.state.active_session_id)
            if self.state.active_session_id
            else None
        )
        try:
            sidebar = self.query_one("#sidebar", SidebarWidget)
            sidebar.refresh_sessions(active_id=self.state.active_session_id)
        except Exception:
            pass
        try:
            chat = self.query_one("#chat", ChatPaneWidget)
            chat.set_show_thinking(self._show_thinking)
            chat.show_session(active)
        except Exception:
            pass
        try:
            composer = self.query_one("#composer", ComposerWidget)
            # Only disable while watching — turn_active does NOT lock the
            # composer; messages typed mid-turn queue locally and flush
            # when the current turn lands (Claude Code style).
            blocked = bool(active and active.mode == SessionMode.WATCHING)
            composer.set_enabled(not blocked)
            recall = (
                [t.user_text for t in active.turns if t.user_text]
                if active is not None
                else []
            )
            composer.set_recall_history(recall)
        except Exception:
            pass

    def _refresh_footer(self) -> None:
        try:
            footer = self.query_one("#footer", FooterStatusWidget)
            footer.update_status()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_sessions(self) -> None:
        rows: list[StoredSession] = []
        for sess in self.state.sessions.values():
            # Only OWNED and WATCHING are reattachable on next launch.
            # DETACHED / CRASHED / CLOSED stay in memory until the user
            # moves them to history.
            if sess.mode not in (SessionMode.OWNED, SessionMode.WATCHING):
                continue
            rows.append(
                StoredSession(
                    session_id=sess.session_id,
                    backend=sess.backend,
                    model=sess.model,
                    cwd=sess.cwd,
                    title=sess.title,
                    options=sess.options,
                    last_seen_seq=sess.last_seen_seq,
                    last_active_at_ms=sess.last_active_at_ms,
                    mode=sess.mode.value,
                )
            )
        try:
            save_sessions(rows)
        except OSError:
            self.state.event_log.append(
                EventLogSource.TUI_INTERNAL, "persistence", "save_sessions failed"
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    def action_save_transcript(self) -> None:
        sid = self.state.active_session_id
        if not sid:
            return
        sess = self.state.sessions.get(sid)
        if sess is None:
            return
        from .persistence import ensure_state_dir, transcript_filename, transcripts_dir
        from .transcript import render as render_transcript

        ensure_state_dir()
        out_dir = transcripts_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / transcript_filename(sess.title or sess.session_id, sess.session_id)
        try:
            path.write_text(render_transcript(sess), encoding="utf-8")
        except OSError as exc:
            self.state.event_log.append(
                EventLogSource.TUI_INTERNAL, "save_transcript", f"failed: {exc}", session_id=sid
            )
            return
        self.state.event_log.append(
            EventLogSource.TUI_INTERNAL, "save_transcript", f"saved → {path}", session_id=sid
        )

    def action_focus_composer(self) -> None:
        try:
            composer = self.query_one("#composer", ComposerWidget)
            composer.query_one("#composer-input").focus()
        except Exception:
            pass

    def action_focus_composer_command(self) -> None:
        """Focus the composer with ``:`` already typed (vim-style)."""
        try:
            composer = self.query_one("#composer", ComposerWidget)
            ta = composer.query_one("#composer-input")
            ta.focus()
            # Pre-fill with the prefix; leave the cursor after it.
            from .commands import PREFIX
            ta.text = PREFIX
            # ``move_cursor`` API lives on TextArea; safely best-effort.
            try:
                ta.move_cursor((0, len(PREFIX)))
            except Exception:
                pass
        except Exception:
            pass

    def action_scroll_up_page(self) -> None:
        self._scroll_chat("scroll_up_page")

    def action_scroll_down_page(self) -> None:
        self._scroll_chat("scroll_down_page")

    def action_scroll_up_line(self) -> None:
        self._scroll_chat("scroll_up_line")

    def action_scroll_down_line(self) -> None:
        self._scroll_chat("scroll_down_line")

    def action_scroll_top(self) -> None:
        self._scroll_chat("scroll_to_top")

    def action_scroll_bottom(self) -> None:
        self._scroll_chat("scroll_to_bottom")

    def _scroll_chat(self, method_name: str) -> None:
        try:
            chat = self.query_one("#chat", ChatPaneWidget)
            getattr(chat, method_name)()
        except Exception:
            pass

    def action_toggle_thinking(self) -> None:
        self._show_thinking = not self._show_thinking
        try:
            chat = self.query_one("#chat", ChatPaneWidget)
            chat.set_show_thinking(self._show_thinking)
        except Exception:
            pass

    def action_next_session(self) -> None:
        self._cycle_session(+1)

    def action_prev_session(self) -> None:
        self._cycle_session(-1)

    def action_select_session(self, index: int) -> None:
        ids = list(self.state.sessions.keys())
        if 1 <= index <= len(ids):
            self.state.active_session_id = ids[index - 1]
            self._refresh_ui()

    def _cycle_session(self, step: int) -> None:
        ids = list(self.state.sessions.keys())
        if not ids:
            return
        if self.state.active_session_id in ids:
            i = ids.index(self.state.active_session_id)
            self.state.active_session_id = ids[(i + step) % len(ids)]
        else:
            self.state.active_session_id = ids[0]
        self._refresh_ui()

    def action_event_log(self) -> None:
        self.push_screen(EventLogOverlay(self.state.event_log))

    def on_footer_status_widget_error_chip_clicked(
        self, _msg: FooterStatusWidget.ErrorChipClicked
    ) -> None:
        self.action_event_log()

    def on_event_log_overlay_saved(self, msg: EventLogOverlay.Saved) -> None:
        self.state.event_log.append(
            EventLogSource.TUI_INTERNAL, "saved", f"event log saved → {msg.path}"
        )

    def action_debug_pane(self) -> None:
        self.push_screen(DebugPane(self._debug_frames))

    def action_new_session(self) -> None:
        backends = list(self.state.daemon.backends.keys()) or ["claude"]
        self.push_screen(NewSessionModal(backends, default_cwd=os.getcwd()))

    def action_attach(self) -> None:
        async def _fetch() -> list[dict[str, Any]]:
            if self._connection is None:
                return []
            return await self._connection.list_sessions(live=True)

        self.push_screen(AttachModal(_fetch))

    async def action_reconnect(self) -> None:
        if self._connection is None:
            return
        await self._connection.stop()
        await self._connection.start()

    async def action_close_session(self) -> None:
        await self._close_active(delete=False)

    async def action_delete_session(self) -> None:
        await self._close_active(delete=True)

    async def action_interrupt(self) -> None:
        sid = self.state.active_session_id
        if sid and self._connection is not None:
            await self._connection.interrupt(sid)

    async def _close_active(self, *, delete: bool) -> None:
        sid = self.state.active_session_id
        if not sid or self._connection is None:
            return
        sess = self.state.sessions.get(sid)
        try:
            await self._connection.close_session(sid, delete=delete)
        finally:
            self._connection.untrack(sid)
            if sess is not None:
                self._archive_to_history(
                    sess, reason="deleted" if delete else "user_closed"
                )
            self.state.sessions.pop(sid, None)
            self.state.active_session_id = None
            self._persist_sessions()
            self._refresh_ui()

    def _archive_to_history(self, sess: SessionState, *, reason: str) -> None:
        record = HistoryRecord(
            session_id=sess.session_id,
            backend=sess.backend,
            title=sess.title or sess.session_id[:8],
            cwd=sess.cwd,
            closed_at_ms=int(time.time() * 1000),
            reason=reason,
        )
        self.state.history.append(record)
        self._persist_history()

    def _persist_history(self) -> None:
        try:
            save_history(
                [
                    HistoryEntry(
                        session_id=r.session_id,
                        backend=r.backend,
                        title=r.title,
                        cwd=r.cwd,
                        closed_at_ms=r.closed_at_ms,
                        reason=r.reason,
                    )
                    for r in self.state.history
                ]
            )
        except OSError:
            self.state.event_log.append(
                EventLogSource.TUI_INTERNAL, "persistence", "save_history failed"
            )

    # ------------------------------------------------------------------
    # Modal results
    # ------------------------------------------------------------------

    async def on_new_session_modal_submit(self, msg: NewSessionModal.Submit) -> None:
        if self._connection is None:
            return
        sid = str(uuid.uuid4())
        backend = msg.backend or "claude"
        options: dict[str, Any] = dict(msg.options or {})
        if msg.model and "model" not in options:
            options["model"] = msg.model
        if msg.cwd and "cwd" not in options:
            options["cwd"] = msg.cwd
        sess = SessionState(
            session_id=sid,
            backend=backend,
            model=msg.model,
            cwd=msg.cwd,
            title=msg.title,
            options=options,
            mode=SessionMode.OWNED,
        )
        self.state.sessions[sid] = sess
        self.state.active_session_id = sid
        self._connection.track_owned(sid, backend=backend, options=options)
        try:
            await self._connection.open_session(sid, backend=backend, options=options)
        except Exception as exc:
            self.state.event_log.append(
                EventLogSource.DAEMON_ERROR, "open_failed", str(exc), session_id=sid
            )
            self.state.sessions.pop(sid, None)
            self._connection.untrack(sid)
            self.state.active_session_id = None
            self._persist_sessions()
            self._refresh_ui()
            return
        self._persist_sessions()
        self._refresh_ui()

    # ------------------------------------------------------------------
    # Watch / takeover button handlers (§8.4, §8.6, §7.6)
    # ------------------------------------------------------------------

    async def on_chat_pane_widget_take_ownership(
        self, msg: ChatPaneWidget.TakeOwnership
    ) -> None:
        await self._take_ownership(msg.session_id)

    async def on_chat_pane_widget_reclaim(self, msg: ChatPaneWidget.Reclaim) -> None:
        await self._take_ownership(msg.session_id)

    async def on_chat_pane_widget_stop_watching(
        self, msg: ChatPaneWidget.StopWatching
    ) -> None:
        sid = msg.session_id
        if self._connection is None:
            return
        try:
            await self._connection.unwatch(sid)
        finally:
            self._connection.untrack(sid)
            sess = self.state.sessions.pop(sid, None)
            if sess and self.config_obj.ui.history_on_unwatch:
                self._archive_to_history(sess, reason="user_closed")
            if self.state.active_session_id == sid:
                self.state.active_session_id = None
            self._persist_sessions()
            self._refresh_ui()

    def on_chat_pane_widget_move_to_history(
        self, msg: ChatPaneWidget.MoveToHistory
    ) -> None:
        sid = msg.session_id
        sess = self.state.sessions.pop(sid, None)
        if sess is not None:
            reason = sess.closed_reason or (
                "session_taken" if sess.mode == SessionMode.DETACHED else "user_closed"
            )
            self._archive_to_history(sess, reason=reason)
        if self._connection is not None:
            self._connection.untrack(sid)
        if self.state.active_session_id == sid:
            self.state.active_session_id = None
        self._persist_sessions()
        self._refresh_ui()

    async def _take_ownership(self, sid: str) -> None:
        if self._connection is None:
            return
        sess = self.state.sessions.get(sid)
        if sess is None:
            return
        try:
            await self._connection.open_session(
                sid,
                backend=sess.backend or "claude",
                options=sess.options,
                resume=True,
                last_seen_seq=sess.last_seen_seq,
            )
        except Exception as exc:
            self.state.event_log.append(
                EventLogSource.DAEMON_ERROR, "take_ownership_failed", str(exc), session_id=sid
            )
            return
        sess.mode = SessionMode.OWNED
        sess.taken_by_pid = None
        self._connection.track_owned(
            sid,
            backend=sess.backend or "claude",
            options=sess.options,
            last_seen_seq=sess.last_seen_seq,
        )
        self._persist_sessions()
        self._refresh_ui()
        self.action_focus_composer()

    async def on_attach_modal_submit(self, msg: AttachModal.Submit) -> None:
        if self._connection is None:
            return
        sid = msg.session_id
        sess = SessionState(session_id=sid, mode=SessionMode.WATCHING)
        self.state.sessions[sid] = sess
        self.state.active_session_id = sid
        self._connection.track_watch(sid)
        try:
            await self._connection.watch_session(sid, last_seen_seq=0)
        except Exception as exc:
            self.state.event_log.append(
                EventLogSource.DAEMON_ERROR, "watch_failed", str(exc), session_id=sid
            )
            self.state.sessions.pop(sid, None)
            self._connection.untrack(sid)
        self._persist_sessions()
        self._refresh_ui()

    def on_composer_widget_changed(self, msg: ComposerWidget.Changed) -> None:
        try:
            popup = self.query_one("#completion", CompletionPopup)
            popup.refresh_for(msg.text)
        except Exception:
            pass

    def on_completion_popup_accepted(self, msg: CompletionPopup.Accepted) -> None:
        try:
            composer = self.query_one("#composer", ComposerWidget)
            composer.set_text(msg.label + " ")
        except Exception:
            pass

    def on_completion_popup_dismissed(self, _msg: CompletionPopup.Dismissed) -> None:
        try:
            composer = self.query_one("#composer", ComposerWidget)
            composer.query_one("#composer-input").focus()
        except Exception:
            pass

    async def on_composer_widget_submit(self, msg: ComposerWidget.Submit) -> None:
        cmd = parse_command(msg.text)
        if cmd is not None and not cmd.is_unknown:
            await self._dispatch_command(cmd)
            return
        if cmd is not None and cmd.is_unknown:
            self.state.event_log.append(
                EventLogSource.TUI_INTERNAL, "command", f"unknown command: {cmd.raw}"
            )
            return
        # Plain text or ``/skill-name`` destined for the active backend goes
        # through verbatim — Claude Code skills + Codex slash commands stay
        # reachable.

        sid = self.state.active_session_id
        if not sid or self._connection is None:
            return
        sess = self.state.sessions.get(sid)
        if sess is not None and sess.turn_active:
            # Agent is mid-turn — queue locally; we'll fire it when
            # agent.result lands. The daemon would reject mid-turn sends
            # with `session_busy`.
            sess.pending_sends.append(msg.text)
            self._refresh_ui()
            return
        await self._send_user_message(sid, msg.text)

    async def _send_user_message(self, sid: str, text: str) -> None:
        if self._connection is None:
            return
        await self._connection.send_user(sid, text)
        # Local echo: the reducer will normally run on the daemon's
        # agent.user_echo, but per spec we render locally regardless.
        sess = self.state.sessions.get(sid)
        if sess is not None:
            reduce_frame(sess, {"type": "agent.user", "message": {"role": "user", "content": text}})
            self._refresh_ui()

    async def _flush_pending_sends(self, sid: str) -> None:
        sess = self.state.sessions.get(sid)
        if sess is None or not sess.pending_sends:
            return
        # Pop the first queued message and fire it; subsequent ones flush
        # on each successive agent.result.
        text = sess.pending_sends.pop(0)
        await self._send_user_message(sid, text)

    async def _dispatch_command(self, cmd) -> None:
        sid = self.state.active_session_id
        sess = self.state.sessions.get(sid) if sid else None

        if cmd.name == "new":
            self.action_new_session()
        elif cmd.name == "help":
            self.action_help()
        elif cmd.name in ("q", "quit"):
            self.exit()
        elif cmd.name == "close":
            await self.action_close_session()
        elif cmd.name == "delete":
            await self.action_delete_session()
        elif cmd.name == "interrupt":
            await self.action_interrupt()
        elif cmd.name == "rename" and sess is not None:
            sess.title = cmd.arg.strip()
            self._persist_sessions()
            self._refresh_ui()
        elif cmd.name == "cwd" and sess is not None:
            sess.cwd = cmd.arg.strip()
            self._refresh_ui()
        elif cmd.name == "model" and sess is not None:
            sess.model = cmd.arg.strip()
            self._refresh_ui()
        elif cmd.name == "watch":
            target = cmd.arg.strip()
            if is_command_uuid(target) and self._connection is not None:
                ws = SessionState(session_id=target, mode=SessionMode.WATCHING)
                self.state.sessions[target] = ws
                self.state.active_session_id = target
                self._connection.track_watch(target)
                try:
                    await self._connection.watch_session(target, last_seen_seq=0)
                except Exception as exc:
                    self.state.event_log.append(
                        EventLogSource.DAEMON_ERROR, "watch_failed", str(exc), session_id=target
                    )
                    self.state.sessions.pop(target, None)
                    self._connection.untrack(target)
                self._persist_sessions()
                self._refresh_ui()
            else:
                self.state.event_log.append(
                    EventLogSource.TUI_INTERNAL,
                    "command",
                    f":watch needs a UUID, got: {target!r}",
                )


# Convenience for ``python -c "from blemees_tui.app import run; run()"``.
def run() -> int:  # pragma: no cover
    app = BlemeesTuiApp()
    app.run()
    return getattr(app, "return_code", 0) or 0


# Module export — keeps the asyncio import alive for type-checking tools that
# walk the file. (No side effects; safe at import time.)
_ = asyncio

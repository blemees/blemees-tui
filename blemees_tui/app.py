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
from textual.widgets import Static

from . import __version__
from .commands import is_uuid as is_command_uuid, parse as parse_command
from .config import Config, apply_cli_overrides, load_config
from .connection import Connection, ConnectionStatus
from .persistence import (
    StoredSession,
    configure_logger,
    load_sessions,
    save_sessions,
)
from .reducer import apply as reduce_frame
from .snapshot import delete_snapshot, load_snapshot, save_snapshot
from .state import (
    AppState,
    DaemonInfo,
    EventLogSource,
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
    TodoPanel,
    TurnStatusBar,
)
from .widgets.modals import AttachModal, HelpModal, NewSessionModal


class BlemeesTuiApp(App):
    """Top-level Textual application."""

    TITLE = "blemees"
    SUB_TITLE = f"v{__version__}"

    CSS = """
    #stack { height: 1fr; width: 100%; }
    #chat-header {
        height: 1;
        width: 100%;
        background: $accent;
        color: auto;
        /* Left padding = sidebar width (28) + the chat pane's own
           horizontal padding (2), so the text lines up over the chat
           transcript rather than the sidebar. */
        padding: 0 2 0 30;
    }
    #main { height: 1fr; width: 100%; }
    #main > #sidebar { width: 28; height: 100%; }
    #main > #chat-column { width: 1fr; height: 100%; }
    #chat-column > #chat { width: 100%; height: 1fr; }
    #chat-column > #completion { width: 100%; height: auto; }
    #chat-column > #todos { width: 100%; height: auto; }
    #chat-column > #turn-status { width: 100%; height: 1; }
    #chat-column > #composer { width: 100%; height: auto; }
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
        # F1..F12 → sessions 1-12. Function keys are universally delivered
        # to the app (no kitty-protocol or terminal-config gymnastics
        # needed). Past 12, Ctrl+Tab cycles or use ``:select N``.
        *[
            Binding(f"f{i}", f"select_session({i})", f"Session {i}", show=False)
            for i in range(1, 13)
        ],
        Binding("t", "toggle_thinking", "Toggle thinking", show=False),
        Binding("m", "toggle_mark", "Mark / unmark for broadcast", show=False),
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
        configure_logger(
            level=self.config_obj.logging.level, keep_days=self.config_obj.logging.keep_days
        )

        self.state = AppState()
        self._connection: Connection | None = None
        self._debug_frames: deque[tuple[str, dict[str, Any]]] = deque(maxlen=DebugPane.CAPACITY)
        self._socket_override = socket_override
        self._show_thinking: bool = self.config_obj.ui.show_thinking
        # Set when a frame-driven UI refresh is already pending — coalesces
        # bursts of frames (replay, fast streaming) into one render per
        # Textual tick instead of N renders.
        self._refresh_pending: bool = False

    # ------------------------------------------------------------------
    # Compose / mount
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield ConnectionBanner(id="conn-banner")
        with Vertical(id="stack"):
            # Full-width session header — text padded to start above the
            # chat pane (past the sidebar). Lives here, not in ChatPaneWidget,
            # so the colored bar reaches the left edge of the screen.
            yield Static("", id="chat-header")
            with Horizontal(id="main"):
                yield SidebarWidget(self.state, id="sidebar")
                # Right column: chat + completion popup + composer stack
                # so the input lines up under the chat pane only, not under
                # the sidebar.
                with Vertical(id="chat-column"):
                    yield ChatPaneWidget(id="chat")
                    yield CompletionPopup(id="completion")
                    yield TodoPanel(self.state, id="todos")
                    yield TurnStatusBar(self.state, id="turn-status")
                    yield ComposerWidget(id="composer")
        yield FooterStatusWidget(self.state, id="footer")

    async def on_mount(self) -> None:
        # Restore each known session from disk. Prefer the full snapshot
        # (turn list, blocks, usage, …) so the chat pane can paint
        # immediately on activation; fall back to the metadata-only row
        # from sessions.json if the snapshot is missing/corrupt.
        for stored in load_sessions():
            cached = load_snapshot(stored.session_id)
            if cached is not None:
                # The disk last_seen_seq is authoritative (the snapshot was
                # taken after we processed those frames). The metadata row
                # may be a tick behind if the TUI crashed between agent.result
                # and the metadata flush — keep the higher of the two.
                cached.last_seen_seq = max(cached.last_seen_seq, stored.last_seen_seq)
                cached.last_active_at_ms = max(cached.last_active_at_ms, stored.last_active_at_ms)
                # The metadata row is the more recent source for marks
                # (rewritten on every change), so prefer it over the
                # snapshot's value.
                cached.marked = stored.marked
                self.state.sessions[cached.session_id] = cached
            else:
                self.state.sessions[stored.session_id] = SessionState(
                    session_id=stored.session_id,
                    backend=stored.backend,
                    model=stored.model,
                    cwd=stored.cwd,
                    title=stored.title,
                    options=stored.options,
                    last_seen_seq=stored.last_seen_seq,
                    last_active_at_ms=stored.last_active_at_ms,
                    mode=SessionMode(stored.mode) if stored.mode else SessionMode.OWNED,
                    marked=stored.marked,
                )

        self._connection = Connection(
            socket_path=self.config_obj.connection.socket or self._socket_override or None,
            on_frame=self._handle_frame,
            on_status_change=self._on_connection_status,
        )

        # Eagerly track every restored session so the supervisor reissues
        # ``open … resume:true, last_seen_seq:<stored>`` (or ``watch``) on
        # connect. We keep eager attach because a session may be actively
        # working in the daemon — without attaching we'd never see its
        # frames, the sidebar wouldn't reflect ``turn_active``, and pending
        # errors would pile up unseen. The snapshot we just loaded already
        # makes this cheap: ``last_seen_seq`` is the high-water mark from
        # the previous run, so the daemon only replays frames since then
        # (typically zero — a no-op replay — or a small delta if the
        # daemon kept running while the TUI was shut down).
        for sess in self.state.sessions.values():
            if sess.mode == SessionMode.WATCHING:
                self._connection.track_watch(sess.session_id, last_seen_seq=sess.last_seen_seq)
            else:
                self._connection.track_owned(
                    sess.session_id,
                    backend=sess.backend,
                    options=sess.options,
                    last_seen_seq=sess.last_seen_seq,
                )

        await self._connection.start()
        self._refresh_ui()

    async def on_unmount(self) -> None:
        if self._connection is not None:
            await self._connection.stop()
        # Final snapshot for every still-live session so the next launch
        # opens with the current transcript instead of an older agent.result
        # boundary.
        for sess in self.state.sessions.values():
            save_snapshot(sess)
        self._persist_sessions()

    # ------------------------------------------------------------------
    # Connection callbacks
    # ------------------------------------------------------------------

    def _handle_frame(self, frame: dict[str, Any]) -> None:
        self._debug_frames.append(("in", frame))
        ftype = frame.get("type", "")
        if ftype == "agent.hello_ack":
            self.state.daemon = DaemonInfo(
                daemon=str(frame.get("daemon", "")),
                protocol=str(frame.get("protocol", "")),
                pid=int(frame.get("pid", 0) or 0),
                backends=dict(frame.get("backends") or {}),
            )
            self.state.event_log.append(
                EventLogSource.CONNECTION, "hello", f"connected to {self.state.daemon.daemon}"
            )
        elif ftype == "agent.error" and "session_id" not in frame:
            # Connection-scope error — log; reducer doesn't see it.
            code = str(frame.get("code", ""))
            msg = str(frame.get("message", ""))
            self.state.event_log.append(EventLogSource.DAEMON_ERROR, code, msg)
            if code in {"slow_consumer", "oversize_message", "daemon_shutdown"}:
                self._latch_connection_fatal(code, msg)

        sid = frame.get("session_id")
        if isinstance(sid, str):
            sess = self.state.sessions.get(sid)
            if sess is None and ftype.startswith(("agent.", "blemees-agentd.")):
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
                    # Snapshot the freshly-completed turn so a TUI restart
                    # can paint it from disk without daemon replay.
                    save_snapshot(sess)
                    if sess is not None and sess.pending_sends:
                        # Flush any locally-queued user messages.
                        asyncio.create_task(self._flush_pending_sends(sid))
                if ftype == "agent.session_closed":
                    self._connection and self._connection.untrack(sid)
                    self.state.sessions.pop(sid, None)
                    delete_snapshot(sid)
                    if self.state.active_session_id == sid:
                        self._set_active_session(None)
                    self._persist_sessions()
                if ftype == "agent.error" and frame.get("code") == "session_unknown":
                    self._connection and self._connection.untrack(sid)
                    self.state.sessions.pop(sid, None)
                    delete_snapshot(sid)
                    if self.state.active_session_id == sid:
                        self._set_active_session(None)
                    self._persist_sessions()

        self._request_refresh()

    def _on_notice(self, session_id: str, frame: dict[str, Any]) -> None:
        category = str(frame.get("category", ""))
        level = str(frame.get("level", "info"))
        text = str(frame.get("text", ""))
        data = frame.get("data") if isinstance(frame.get("data"), dict) else {}
        self.state.event_log.append(EventLogSource.NOTICE, category, text, session_id=session_id)
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

    def _request_refresh(self) -> None:
        """Coalesce frame-driven UI refreshes — schedule at most one
        ``_refresh_ui`` per Textual tick. A burst of replay frames in a
        single tick batches into one render instead of N."""
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.call_after_refresh(self._flush_refresh)

    def _flush_refresh(self) -> None:
        self._refresh_pending = False
        self._refresh_ui()

    def _refresh_ui(self) -> None:
        self._refresh_footer()
        self._refresh_header()
        self._refresh_turn_status()
        self._refresh_todo_panel()
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
                [t.user_text for t in active.turns if t.user_text] if active is not None else []
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

    def _refresh_turn_status(self) -> None:
        try:
            bar = self.query_one("#turn-status", TurnStatusBar)
            bar.update_status()
        except Exception:
            pass

    def _refresh_todo_panel(self) -> None:
        try:
            panel = self.query_one("#todos", TodoPanel)
            panel.update_status()
        except Exception:
            pass

    def _set_active_session(self, sid: str | None) -> None:
        """Single point for active-session changes.

        Snapshots the composer's current text into the previous session's
        ``draft`` (so unsubmitted text travels with the session, not the
        composer), then loads the new session's draft into the composer.
        Closed sessions — those already removed from ``state.sessions`` by
        the time we get here — don't get their draft saved.
        """
        prev_sid = self.state.active_session_id
        composer: ComposerWidget | None
        try:
            composer = self.query_one("#composer", ComposerWidget)
        except Exception:
            composer = None

        if composer is not None and prev_sid is not None and prev_sid != sid:
            prev = self.state.sessions.get(prev_sid)
            if prev is not None:
                try:
                    ta = composer.query_one("#composer-input")
                    prev.draft = ta.text
                except Exception:
                    pass

        self.state.active_session_id = sid

        if composer is not None:
            new_sess = self.state.sessions.get(sid) if sid else None
            composer.set_text(new_sess.draft if new_sess is not None else "", focus=False)

    def _refresh_header(self) -> None:
        try:
            header = self.query_one("#chat-header", Static)
        except Exception:
            return
        active = (
            self.state.sessions.get(self.state.active_session_id)
            if self.state.active_session_id
            else None
        )
        if active is None:
            header.update("[dim]No session[/]")
            return
        title = active.title or active.session_id[:8]
        # Model moved to TurnStatusBar (bottom-right, beside the turn
        # count); chat-header keeps just the session title + cwd.
        text = f"[b]{_escape_markup(title)}[/]"
        if active.cwd:
            text += f"  · {_escape_markup(active.cwd)}"
        header.update(text)

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
                    marked=sess.marked,
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

    def action_toggle_mark(self) -> None:
        """Toggle the broadcast-mark on the active session.

        Mark a few sessions with ``m`` (or ``:mark``), then start a
        composer message with ``>> `` to fan it out to all of them.
        """
        sid = self.state.active_session_id
        if not sid:
            return
        sess = self.state.sessions.get(sid)
        if sess is None:
            return
        sess.marked = not sess.marked
        self._persist_sessions()
        self._refresh_ui()

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
            self._set_active_session(ids[index - 1])
            self._refresh_ui()

    def _cycle_session(self, step: int) -> None:
        ids = list(self.state.sessions.keys())
        if not ids:
            return
        if self.state.active_session_id in ids:
            i = ids.index(self.state.active_session_id)
            self._set_active_session(ids[(i + step) % len(ids)])
        else:
            self._set_active_session(ids[0])
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
        if sid is not None:
            await self._interrupt_session(sid)

    async def _interrupt_session(self, sid: str) -> None:
        if self._connection is not None:
            await self._connection.interrupt(sid)

    async def _close_active(self, *, delete: bool) -> None:
        sid = self.state.active_session_id
        if sid is None:
            return
        await self._close_session_by_id(sid, delete=delete)
        self._refresh_ui()

    async def _close_session_by_id(self, sid: str, *, delete: bool) -> None:
        """Close (or delete) a single session by id. Used both by the
        active-session keybindings and the multi-target ``:close`` /
        ``:delete`` commands."""
        if self._connection is None:
            return
        try:
            await self._connection.close_session(sid, delete=delete)
        finally:
            self._connection.untrack(sid)
            self.state.sessions.pop(sid, None)
            delete_snapshot(sid)
            if self.state.active_session_id == sid:
                self._set_active_session(None)
            self._persist_sessions()

    def _resolve_session_indices(self, arg: str) -> tuple[list[str], list[str]]:
        """Parse a space-separated list of 1-indexed session numbers into
        ``(session_ids, errors)``.

        Empty arg falls back to ``[active_session_id]`` (or ``[]`` if
        nothing's active). Non-numeric or out-of-range tokens are
        collected as human-readable error strings so the caller can log
        them and still proceed with the resolved entries.
        """
        arg = (arg or "").strip()
        if not arg:
            sid = self.state.active_session_id
            return ([sid] if sid else []), []

        ids_in_order = list(self.state.sessions.keys())
        resolved: list[str] = []
        errors: list[str] = []
        for token in arg.split():
            try:
                n = int(token)
            except ValueError:
                errors.append(f"invalid session index: {token!r}")
                continue
            if not (1 <= n <= len(ids_in_order)):
                errors.append(f"session index out of range: {n}")
                continue
            resolved.append(ids_in_order[n - 1])
        return resolved, errors

    def _split_indices_and_value(self, arg: str) -> tuple[list[str], list[str], str]:
        """Split a value-command arg like ``"1 3 my new title"`` into
        leading session indices + trailing value.

        Returns ``(session_ids, errors, value)``. If no leading numeric
        tokens are present, the target defaults to the active session and
        the entire arg becomes the value. ``"1 3 hi"`` → sessions 1 & 3,
        value ``"hi"``. ``"hi"`` → active session, value ``"hi"``. An
        empty arg → empty ids, empty errors, empty value.

        Quirk: a value that is itself a bare integer (e.g. ``:rename 5``)
        is interpreted as an index, not a value. Lead with a non-digit
        character if you really want to set the title to ``"5"``.
        """
        arg = (arg or "").strip()
        if not arg:
            return [], [], ""

        tokens = arg.split()
        numeric_prefix = 0
        for token in tokens:
            try:
                int(token)
                numeric_prefix += 1
            except ValueError:
                break

        if numeric_prefix == 0:
            sid = self.state.active_session_id
            return ([sid] if sid else []), [], arg

        ids_in_order = list(self.state.sessions.keys())
        resolved: list[str] = []
        errors: list[str] = []
        for token in tokens[:numeric_prefix]:
            n = int(token)
            if not (1 <= n <= len(ids_in_order)):
                errors.append(f"session index out of range: {n}")
                continue
            resolved.append(ids_in_order[n - 1])

        value = " ".join(tokens[numeric_prefix:])
        return resolved, errors, value

    def _log_command_errors(self, errors: list[str]) -> None:
        for err in errors:
            self.state.event_log.append(EventLogSource.TUI_INTERNAL, "command", err)

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
        self._set_active_session(sid)
        self._connection.track_owned(sid, backend=backend, options=options)
        try:
            await self._connection.open_session(
                sid,
                backend=backend,
                options=options,
                alias=msg.title or None,
            )
        except Exception as exc:
            self.state.event_log.append(
                EventLogSource.DAEMON_ERROR, "open_failed", str(exc), session_id=sid
            )
            self.state.sessions.pop(sid, None)
            self._connection.untrack(sid)
            self._set_active_session(None)
            self._persist_sessions()
            self._refresh_ui()
            return
        if msg.peer_mcp_attached:
            interval = (msg.peer_poll_interval or "15m").strip() or "15m"
            bootstrap = f"/loop {interval} check your peer inbox"
            if sess.turn_active:
                sess.pending_sends.append(bootstrap)
            else:
                await self._send_user_message(sid, bootstrap)
        self._persist_sessions()
        self._refresh_ui()

    # ------------------------------------------------------------------
    # Watch / takeover button handlers (§8.4, §8.6, §7.6)
    # ------------------------------------------------------------------

    async def on_chat_pane_widget_take_ownership(self, msg: ChatPaneWidget.TakeOwnership) -> None:
        await self._take_ownership(msg.session_id)

    async def on_chat_pane_widget_reclaim(self, msg: ChatPaneWidget.Reclaim) -> None:
        await self._take_ownership(msg.session_id)

    async def on_chat_pane_widget_stop_watching(self, msg: ChatPaneWidget.StopWatching) -> None:
        sid = msg.session_id
        if self._connection is None:
            return
        try:
            await self._connection.unwatch(sid)
        finally:
            self._connection.untrack(sid)
            self.state.sessions.pop(sid, None)
            delete_snapshot(sid)
            if self.state.active_session_id == sid:
                self._set_active_session(None)
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
        self._set_active_session(sid)
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
        # `>> message` fans out to every marked session. Strip the prefix
        # before sending. Detected before slash/`:` parsing so the broadcast
        # path doesn't pick up TUI commands by accident.
        if msg.text.startswith(">> "):
            await self._broadcast(msg.text[3:])
            return

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

    async def _broadcast(self, text: str) -> None:
        """Fan a message out to every marked OWNED session.

        WATCHING / CRASHED / CLOSED / DETACHED sessions are silently
        excluded. Busy recipients get the message queued via the existing
        ``pending_sends`` flush path so it lands when their current turn
        ends. Slash and TUI-command broadcasts are blocked — too easy a
        foot-gun.
        """
        body = text.strip()
        if not body:
            return
        if body.startswith(("/", ":")):
            self.state.event_log.append(
                EventLogSource.TUI_INTERNAL,
                "broadcast",
                f"slash/colon commands don't broadcast (blocked: {body[:32]!r})",
            )
            return

        recipients: list[SessionState] = [
            s for s in self.state.sessions.values() if s.marked and s.mode == SessionMode.OWNED
        ]
        if not recipients:
            self.state.event_log.append(
                EventLogSource.TUI_INTERNAL,
                "broadcast",
                "no marked sessions — press `m` (or `:mark`) on the sessions you want",
            )
            return

        skipped = sum(
            1 for s in self.state.sessions.values() if s.marked and s.mode != SessionMode.OWNED
        )
        sent = 0
        queued = 0
        for sess in recipients:
            if sess.turn_active:
                sess.pending_sends.append(body)
                queued += 1
            else:
                await self._send_user_message(sess.session_id, body)
                sent += 1

        bits = [f"sent to {sent}"] if sent else []
        if queued:
            bits.append(f"queued for {queued}")
        if skipped:
            bits.append(f"skipped {skipped} non-owned")
        summary = " · ".join(bits) if bits else "no recipients"
        self.state.event_log.append(EventLogSource.TUI_INTERNAL, "broadcast", summary)
        self._refresh_ui()

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
        if cmd.name == "new":
            self.action_new_session()
        elif cmd.name == "help":
            self.action_help()
        elif cmd.name in ("q", "quit"):
            self.exit()
        elif cmd.name == "close":
            ids, errors = self._resolve_session_indices(cmd.arg)
            self._log_command_errors(errors)
            for target_sid in ids:
                await self._close_session_by_id(target_sid, delete=False)
            if ids:
                self._refresh_ui()
        elif cmd.name == "delete":
            ids, errors = self._resolve_session_indices(cmd.arg)
            self._log_command_errors(errors)
            for target_sid in ids:
                await self._close_session_by_id(target_sid, delete=True)
            if ids:
                self._refresh_ui()
        elif cmd.name == "interrupt":
            ids, errors = self._resolve_session_indices(cmd.arg)
            self._log_command_errors(errors)
            for target_sid in ids:
                await self._interrupt_session(target_sid)
        elif cmd.name == "rename":
            ids, errors, value = self._split_indices_and_value(cmd.arg)
            self._log_command_errors(errors)
            changed = False
            for target_sid in ids:
                target = self.state.sessions.get(target_sid)
                if target is not None:
                    target.title = value
                    changed = True
            if changed:
                self._persist_sessions()
                self._refresh_ui()
        elif cmd.name == "cwd":
            ids, errors, value = self._split_indices_and_value(cmd.arg)
            self._log_command_errors(errors)
            changed = False
            for target_sid in ids:
                target = self.state.sessions.get(target_sid)
                if target is not None:
                    target.cwd = value
                    changed = True
            if changed:
                self._refresh_ui()
        elif cmd.name == "model":
            ids, errors, value = self._split_indices_and_value(cmd.arg)
            self._log_command_errors(errors)
            changed = False
            for target_sid in ids:
                target = self.state.sessions.get(target_sid)
                if target is not None:
                    target.model = value
                    changed = True
            if changed:
                self._refresh_ui()
        elif cmd.name == "select":
            arg = cmd.arg.strip()
            try:
                index = int(arg)
            except ValueError:
                self.state.event_log.append(
                    EventLogSource.TUI_INTERNAL,
                    "command",
                    f":select needs a number, got: {arg!r}",
                )
                return
            self.action_select_session(index)
        elif cmd.name == "mark":
            arg_lower = cmd.arg.strip().lower()
            if arg_lower == "all":
                for s in self.state.sessions.values():
                    if s.mode == SessionMode.OWNED:
                        s.marked = True
                self._persist_sessions()
                self._refresh_ui()
            elif arg_lower in ("clear", "none"):
                for s in self.state.sessions.values():
                    s.marked = False
                self._persist_sessions()
                self._refresh_ui()
            else:
                # Empty arg → toggle active. Numeric tokens → toggle each.
                ids, errors = self._resolve_session_indices(cmd.arg)
                self._log_command_errors(errors)
                changed = False
                for target_sid in ids:
                    target = self.state.sessions.get(target_sid)
                    if target is not None:
                        target.marked = not target.marked
                        changed = True
                if changed:
                    self._persist_sessions()
                    self._refresh_ui()
        elif cmd.name == "watch":
            target = cmd.arg.strip()
            if is_command_uuid(target) and self._connection is not None:
                ws = SessionState(session_id=target, mode=SessionMode.WATCHING)
                self.state.sessions[target] = ws
                self._set_active_session(target)
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


def _escape_markup(text: str) -> str:
    """Escape Rich markup characters so user-controlled strings (titles,
    cwd, model names) can't inject tags into the header."""
    return text.replace("[", r"\[")


# Convenience for ``python -c "from blemees_tui.app import run; run()"``.
def run() -> int:  # pragma: no cover
    app = BlemeesTuiApp()
    app.run()
    return getattr(app, "return_code", 0) or 0


# Module export — keeps the asyncio import alive for type-checking tools that
# walk the file. (No side effects; safe at import time.)
_ = asyncio

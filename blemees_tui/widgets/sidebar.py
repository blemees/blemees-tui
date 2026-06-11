"""Sidebar — sessions list (spec §11)."""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

from rich.markup import escape as rich_escape
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Label, Static

from ..state import AppState, SessionMode


class SidebarWidget(Widget):
    """Read-only sessions index. Switching sessions is keyboard-driven
    (``1``–``9`` and ``Ctrl+Tab``), so the rows are plain Static widgets —
    no ListView focus or selection noise.

    Live sessions are grouped by ``cwd`` under a dim path header so it's
    obvious which sessions belong to which project. The numeric index next
    to each row is the global insertion-order index (matches ``F<N>`` and
    ``:select N``) — grouping is visual only and does not renumber."""

    DEFAULT_CSS = """
    SidebarWidget .-hidden { display: none; }
    SidebarWidget {
        width: 28;
        border-right: tall $accent;
    }
    SidebarWidget Static.row { height: 1; padding: 0 1; }
    SidebarWidget Static.section { height: 1; padding: 0 1; color: $text-muted; }
    SidebarWidget Static.cwd-header { height: 1; padding: 0 1; color: $text-muted; }
    """

    def __init__(self, state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state = state

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[b]Sessions[/b]")
            yield Label("[dim]Ctrl+N · new[/]")
            yield Label("[dim]Ctrl+T · attach[/]")
            yield Static("─ attention ─", classes="section -hidden", id="sidebar-attn-header")
            yield Vertical(id="sidebar-attn", classes="-hidden")
            yield Static("─ live ─", classes="section", id="sidebar-live-header")
            yield Vertical(id="sidebar-live")

    def refresh_sessions(self, *, active_id: str | None = None) -> None:
        live = self.query_one("#sidebar-live", Vertical)
        live.remove_children()
        self._refresh_attention(active_id)

        # Group by cwd while preserving insertion order — both for the
        # groups themselves (first-seen cwd appears first) and for sessions
        # within a group. Index is taken from the global enumeration so it
        # still matches F<N> / :select N.
        groups: OrderedDict[str, list[tuple[int, str, object]]] = OrderedDict()
        for idx, (sid, sess) in enumerate(self._state.sessions.items(), start=1):
            groups.setdefault(sess.cwd or "", []).append((idx, sid, sess))

        for cwd, members in groups.items():
            live.mount(Static(f"[dim]{_escape_markup(_format_cwd(cwd))}[/]", classes="cwd-header"))
            for idx, sid, sess in members:
                icon = _mode_icon(sess.mode)
                label = _escape_markup(sess.title or sid[:8])
                busy = sess.turn_active
                # Leading mark glyph (◆ when marked for ``>>`` broadcast,
                # space gap otherwise so all rows align).
                mark = "[$accent]◆[/]" if sess.marked else " "
                # Attention badge (#4): a pending permission or a needs_attention
                # flag the owner should act on, shown for background sessions too.
                badge = ""
                if sess.pending_permission or sess.needs_attention:
                    badge = " [$error bold]●[/]"
                row = f"{mark} {idx} {icon} {label}{badge}"
                if sid == active_id:
                    row = f"{mark} [reverse] {idx} [/] {icon} {label}{badge}"
                if busy:
                    # $warning tint signals "agent is working" without the
                    # noise of an extra glyph. Matches TurnStatusBar's
                    # in-flight color so the two read as the same state.
                    row = f"[$warning]{row}[/]"
                live.mount(Static(row, classes="row"))

    def _refresh_attention(self, active_id: str | None) -> None:
        """The inbox section (#22): tier 0 (blocked) then tier 1 (ready),
        insertion-order stable within each tier — membership changes only on
        state transitions, never during streaming."""
        attn = self.query_one("#sidebar-attn", Vertical)
        header = self.query_one("#sidebar-attn-header", Static)
        attn.remove_children()
        rows = [
            (attention_tier(sess), idx, sid, sess)
            for idx, (sid, sess) in enumerate(self._state.sessions.items(), start=1)
            if attention_tier(sess) <= 1
        ]
        rows.sort(key=lambda r: (r[0], r[1]))
        attn.set_class(not rows, "-hidden")
        header.set_class(not rows, "-hidden")
        for _tier, idx, sid, sess in rows:
            label = _escape_markup(sess.title or sid[:8])
            badge = attention_badge(sess)
            row = f" {idx} {label}  {badge}"
            if sid == active_id:
                row = f" [reverse] {idx} [/] {label}  {badge}"
            attn.mount(Static(row, classes="row"))


def attention_tier(sess) -> int:
    """Pure inbox tiering (#22): 0 = blocked (needs the owner to proceed),
    1 = ready for you (finished while backgrounded), 2 = busy, 3 = idle."""
    if sess.pending_permission or sess.needs_attention:
        return 0
    if sess.ready_for_you:
        return 1
    if sess.turn_active:
        return 2
    return 3


def attention_badge(sess) -> str:
    """Short reason chip for the inbox row."""
    if sess.pending_permission:
        return "[$error bold]⏳ permission[/]"
    if sess.needs_attention:
        reason = (sess.attention_reason or "attention").replace("_", " ")
        return f"[$error bold]⚠ {_escape_markup(reason)}[/]"
    if sess.ready_for_you:
        return "[$success]● done[/]"
    return ""


def _mode_icon(mode: SessionMode) -> str:
    return {
        SessionMode.OWNED: "●",
        SessionMode.WATCHING: "👀",
        SessionMode.DETACHED: "⊘",
        SessionMode.CRASHED: "✗",
        SessionMode.CLOSED: "✓",
    }.get(mode, "·")


def _format_cwd(cwd: str) -> str:
    """Compact display for a session cwd in the 28-wide sidebar.

    Empty cwd → ``(no cwd)``. Paths under ``$HOME`` are collapsed to
    ``~/…``. Long paths fall back to ``…/<last two components>`` so the
    project folder stays visible."""
    if not cwd:
        return "(no cwd)"
    home = str(Path.home())
    if cwd == home:
        return "~"
    if cwd.startswith(home + os.sep):
        cwd = "~" + cwd[len(home) :]
    if len(cwd) <= 26:
        return cwd
    parts = cwd.split(os.sep)
    tail = os.sep.join(parts[-2:]) if len(parts) >= 2 else parts[-1]
    return f"…/{tail}"


def _escape_markup(text: str) -> str:
    """Escape Rich markup in user-controlled text (paths, titles) — rich's
    own escaper, not a naive ``[``-replace (#16)."""
    return rich_escape(text)

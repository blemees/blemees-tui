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

    Sessions are scoped to the active profile (``daemon.active_profile``) and
    nested under their agent: each agent from the profile's roster is a
    header, and that agent's live sessions are indented beneath it. The
    numeric index next to each session is its position in the *visible*
    (current-profile) ordering, so it matches ``F<N>`` / ``:select N`` /
    ``Ctrl+Tab``, which resolve against the same scope. Agents with no
    sessions yet still show (dimmed) so the roster is visible up front."""

    DEFAULT_CSS = """
    SidebarWidget {
        width: 28;
        border-right: tall $accent;
    }
    SidebarWidget Static.row { height: 1; padding: 0 1; }
    SidebarWidget Static.session { height: 1; padding: 0 1 0 3; }
    SidebarWidget Static.agent-header { height: 1; padding: 0 1; }
    SidebarWidget Static.profile { height: 1; padding: 0 1; color: $text-muted; }
    """

    def __init__(self, state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state = state

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[b]Sessions[/b]")
            yield Label("[dim]Ctrl+N · new[/]")
            yield Label("[dim]Ctrl+T · attach[/]")
            yield Static("", classes="profile", id="sidebar-profile")
            yield Vertical(id="sidebar-tree")

    def refresh_sessions(self, *, active_id: str | None = None) -> None:
        prof = self._state.daemon.active_profile
        profile_line = self.query_one("#sidebar-profile", Static)
        profile_line.display = bool(prof)
        if prof:
            profile_line.update(f"[dim]profile: {_escape_markup(prof)}[/]")

        tree = self.query_one("#sidebar-tree", Vertical)
        tree.remove_children()

        # Visible = active-profile sessions, in the same order F<N> resolves.
        # The numeric label is the 1-based position in this list.
        by_agent: OrderedDict[str, list[tuple[int, str, object]]] = OrderedDict()
        active_agent: str | None = None
        for idx, (sid, sess) in enumerate(self._state.visible_session_items(), start=1):
            by_agent.setdefault(sess.agent or "", []).append((idx, sid, sess))
            if sid == active_id:
                active_agent = sess.agent or ""

        # Agent order: the roster first (so every configured agent shows, even
        # sessionless ones), then any agent that has visible sessions but isn't
        # in the roster (defensive — shouldn't normally happen within a
        # profile). Empty-agent sessions sort last under a placeholder header.
        order: list[str] = []
        for entry in self._state.daemon.agent_roster:
            name = str(entry.get("name", ""))
            if name and name not in order:
                order.append(name)
        for name in by_agent:
            if name and name not in order:
                order.append(name)
        if "" in by_agent:
            order.append("")

        for name in order:
            members = by_agent.get(name, [])
            live_count = sum(
                1 for _, _, s in members if s.mode != SessionMode.CLOSED
            )
            label = _escape_markup(name) if name else "(no agent)"
            count_txt = f" [dim]({live_count})[/]" if live_count else ""
            if members:
                header = f"● {label}{count_txt}"
            else:
                # Dim agents that have no session yet.
                header = f"[dim]○ {label}[/]"
            if name == active_agent:
                header = f"[reverse] {label} [/]{count_txt}"
            tree.mount(Static(header, classes="agent-header"))

            for idx, sid, sess in members:
                tree.mount(Static(_session_row(idx, sid, sess, active_id), classes="session"))


def _session_row(idx: int, sid: str, sess, active_id: str | None) -> str:
    icon = _mode_icon(sess.mode)
    label = _escape_markup(sess.title or sid[:8])
    cwd_txt = f" [dim]{_escape_markup(_format_cwd(sess.cwd))}[/]" if sess.cwd else ""
    # Leading mark glyph (◆ when marked for ``>>`` broadcast, space gap
    # otherwise so all rows align).
    mark = "[$accent]◆[/]" if sess.marked else " "
    # Attention badge (#4): a pending permission or a needs_attention flag the
    # owner should act on, shown for background sessions too.
    badge = ""
    if sess.pending_permission or sess.needs_attention:
        badge = " [$error bold]●[/]"
    if sid == active_id:
        row = f"{mark} [reverse] {idx} [/] {icon} {label}{badge}{cwd_txt}"
    else:
        row = f"{mark} {idx} {icon} {label}{badge}{cwd_txt}"
    if sess.turn_active:
        # $warning tint signals "agent is working" without the noise of an
        # extra glyph. Matches TurnStatusBar's in-flight color.
        row = f"[$warning]{row}[/]"
    return row


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

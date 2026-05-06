"""Sidebar — sessions list (spec §11)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Label, Static

from ..state import AppState, SessionMode


class SidebarWidget(Widget):
    """Read-only sessions + history index. Switching sessions is keyboard-
    driven (``1``–``9`` and ``Ctrl+Tab``), so the rows are plain Static
    widgets — no ListView focus or selection noise."""

    DEFAULT_CSS = """
    SidebarWidget {
        width: 28;
        border-right: tall $accent;
    }
    SidebarWidget Static.row { height: 1; padding: 0 1; }
    SidebarWidget Static.section { height: 1; padding: 0 1; color: $text-muted; }
    """

    def __init__(self, state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state = state

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[b]Sessions[/b]")
            yield Label("[dim]Ctrl+N · new[/]")
            yield Label("[dim]Ctrl+T · attach[/]")
            yield Static("─ live ─", classes="section", id="sidebar-live-header")
            yield Vertical(id="sidebar-live")
            yield Static("─ history ─", classes="section", id="sidebar-history-header")
            yield Vertical(id="sidebar-history")

    def refresh_sessions(self, *, active_id: str | None = None) -> None:
        live = self.query_one("#sidebar-live", Vertical)
        live.remove_children()
        for idx, (sid, sess) in enumerate(self._state.sessions.items(), start=1):
            icon = _mode_icon(sess.mode)
            label = sess.title or sid[:8]
            busy = sess.turn_active
            # Leading mark glyph (◆ when marked for ``>>`` broadcast,
            # space gap otherwise so all rows align).
            mark = "[$accent]◆[/]" if sess.marked else " "
            row = f"{mark} {idx} {icon} {label}"
            if sid == active_id:
                row = f"{mark} [reverse] {idx} [/] {icon} {label}"
            if busy:
                # Yellow tint signals "agent is working" without the noise
                # of an extra glyph.
                row = f"[yellow]{row}[/]"
            live.mount(Static(row, classes="row"))
        history = self.query_one("#sidebar-history", Vertical)
        history.remove_children()
        for entry in self._state.history[-50:][::-1]:
            history.mount(Static(f"⊘ {entry.title or entry.session_id[:8]}", classes="row"))


def _mode_icon(mode: SessionMode) -> str:
    return {
        SessionMode.OWNED: "●",
        SessionMode.WATCHING: "👀",
        SessionMode.DETACHED: "⊘",
        SessionMode.CRASHED: "✗",
        SessionMode.CLOSED: "✓",
    }.get(mode, "·")

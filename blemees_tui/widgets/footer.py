"""Footer status chip (spec §14.1).

Single-line status bar docked at the bottom, split into three sections so
each piece sits under the column it describes:

* **Left** — sidebar-aligned: the connection state chip (``● connected``
  / ``● reconnecting`` / …). Sits in the same column as the sidebar.
* **Middle** — text-area-aligned: ``blemees v…``, the active session's
  agent version (``claude v…`` / ``codex v…``), and any rate-limit
  notice. Starts at the column where the chat / text input begins
  (sidebar + chat-pane padding). The context-window meter lives on the
  ``TurnStatusBar`` (right of turns) — it isn't duplicated here.
* **Right** — pinned: the ``! N errors`` chip. Clicking anywhere on the
  footer when errors are pending opens the event log.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from .. import __version__
from ..state import AppState


class FooterStatusWidget(Widget):
    # Width of the left section = sidebar width (28); the middle section
    # adds left padding equal to the chat pane's own left padding (2),
    # so its content lines up with the chat transcript and composer
    # text — same alignment trick as ``#chat-header``.
    DEFAULT_CSS = """
    FooterStatusWidget {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text-muted;
    }
    FooterStatusWidget > Horizontal { height: 1; width: 100%; }
    FooterStatusWidget Static { height: 1; }
    FooterStatusWidget #footer-status {
        width: 28;
        content-align: left middle;
        padding: 0 1;
    }
    FooterStatusWidget #footer-info {
        width: 1fr;
        content-align: left middle;
        padding: 0 1 0 2;
    }
    FooterStatusWidget #footer-errors {
        width: auto;
        content-align: right middle;
        padding: 0 1;
    }
    """

    class ErrorChipClicked(Message):
        """Posted when the user clicks the ``! N errors`` chip — app opens the event log."""

    def __init__(self, state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state = state

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("", id="footer-status")
            yield Static("", id="footer-info")
            yield Static("", id="footer-errors")

    def on_mount(self) -> None:
        self.update_status()

    async def on_click(self) -> None:
        # Cheap: any click on the footer with active errors opens the log.
        if any(s.pending_errors for s in self._state.sessions.values()):
            self.post_message(self.ErrorChipClicked())

    def update_status(self) -> None:
        s = self._state
        # Theme tokens ($success / $warning / $error) instead of ANSI
        # bright_X — the bright variants washed out against the panel bg
        # in some terminals, leaving the dot invisible. The label after
        # the dot keeps the state legible even if the glyph still trips
        # over a symbol-font in some terminals.
        status_chip = {
            "connected": "[$success bold]● connected[/]",
            "reconnecting": "[$warning bold]● reconnecting[/]",
            "disconnected": "[$error bold]● disconnected[/]",
            "fatal": "[$error bold]✗ fatal[/]",
        }.get(s.connection_status, "[dim]· …[/]")
        active = s.sessions.get(s.active_session_id) if s.active_session_id else None
        agents = s.daemon.agents or {}
        if active is not None and active.backend:
            # blemees/3: a session's `backend` field carries its profile name.
            agent_label = active.backend
            # Append the agent's current mode (ACP current_mode_update, #2).
            if active.current_mode:
                agent_label += f" · {active.current_mode}"
        else:
            # hello_ack's agents map carries availability strings ("available"),
            # not versions — only "v"-prefix values that look like versions (#25).
            agent_bits = (
                [f"{k} v{v}" if v and v[0].isdigit() else f"{k} {v}" for k, v in agents.items()]
                if agents
                else []
            )
            agent_label = " · ".join(agent_bits) if agent_bits else "no agent"
        rate_chip = ""
        if s.rate_limits and s.rate_limits.text:
            colour = "yellow" if s.rate_limits.level == "warn" else "white"
            rate_chip = f"[{colour}]↺ {s.rate_limits.text}[/]"
        info_bits = [f"[dim]blemees v{__version__}[/]", f"[dim]{agent_label}[/]"]
        if rate_chip:
            info_bits.append(rate_chip)
        info_text = " · ".join(info_bits)
        errors = sum(len(sess.pending_errors) for sess in s.sessions.values())
        errors_text = f"[red]! {errors} errors[/]" if errors else ""
        try:
            self.query_one("#footer-status", Static).update(status_chip)
            self.query_one("#footer-info", Static).update(info_text)
            self.query_one("#footer-errors", Static).update(errors_text)
        except Exception:
            pass

"""Footer status chip (spec §14.1).

Single-line status bar docked at the bottom, split into three sections so
each piece sits under the column it describes:

* **Left** — sidebar-aligned: the connection state chip (``● connected``
  / ``● reconnecting`` / …). Sits in the same column as the sidebar.
* **Middle** — text-area-aligned: ``blemees v…``, the active session's
  agent version (``claude v…`` / ``codex v…``), context-window meter,
  and any rate-limit notice. Starts at the column where the chat / text
  input begins (sidebar + chat-pane padding).
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
        backends = s.daemon.backends or {}
        if active is not None and active.backend:
            version = backends.get(active.backend, "")
            agent_label = f"{active.backend} v{version}" if version else active.backend
        else:
            backend_bits = [f"{k} v{v}" for k, v in backends.items()] if backends else []
            agent_label = " · ".join(backend_bits) if backend_bits else "no agent"
        ctx = ""
        if active and active.context_window:
            ctx = f"ctx {active.context_tokens // 1000}k/{active.context_window // 1000}k"
        rate_chip = ""
        if s.rate_limits and s.rate_limits.text:
            colour = "yellow" if s.rate_limits.level == "warn" else "white"
            rate_chip = f"[{colour}]↺ {s.rate_limits.text}[/]"
        info_bits = [f"[dim]blemees v{__version__}[/]", f"[dim]{agent_label}[/]"]
        if ctx:
            info_bits.append(ctx)
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

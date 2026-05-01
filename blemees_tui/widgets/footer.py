"""Footer status chip (spec §14.1)."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import Static

from ..state import AppState


class FooterStatusWidget(Static):
    DEFAULT_CSS = """
    FooterStatusWidget { dock: bottom; height: 1; padding: 0 1; }
    """

    class ErrorChipClicked(Message):
        """Posted when the user clicks the ``! N errors`` chip — app opens the event log."""

    def __init__(self, state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state = state
        self.update_status()

    async def on_click(self) -> None:
        # Cheap: any click on the footer with active errors opens the log.
        if any(s.pending_errors for s in self._state.sessions.values()):
            self.post_message(self.ErrorChipClicked())

    def update_status(self) -> None:
        s = self._state
        dot = {
            "connected": "[green]●[/]",
            "reconnecting": "[yellow]●[/]",
            "disconnected": "[red]●[/]",
            "fatal": "[red]✗[/]",
        }.get(s.connection_status, "·")
        backends = " ".join(f"{k} {v}" for k, v in (s.daemon.backends or {}).items())
        turns = sum(len(sess.turns) for sess in s.sessions.values())
        active = s.sessions.get(s.active_session_id) if s.active_session_id else None
        ctx = ""
        if active and active.context_window:
            ctx = f" · ctx {active.context_tokens // 1000}k/{active.context_window // 1000}k"
        errors = sum(len(sess.pending_errors) for sess in s.sessions.values())
        err_chip = f" · [red]! {errors} errors[/]" if errors else ""
        rate_chip = ""
        if s.rate_limits and s.rate_limits.text:
            colour = "yellow" if s.rate_limits.level == "warn" else "white"
            rate_chip = f" · [{colour}]↺ {s.rate_limits.text}[/]"
        self.update(
            f"{dot} {s.daemon.daemon or 'daemon ?'} · {backends or 'no backends'} · "
            f"{turns} turns{ctx}{err_chip}{rate_chip}"
        )

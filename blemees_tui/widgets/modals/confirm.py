"""Yes/no confirm modal — guards destructive keys (e.g. Ctrl+Shift+W delete)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class ConfirmModal(ModalScreen[bool]):
    """Dismisses with ``True`` on y / Enter, ``False`` on n / Esc."""

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("enter", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "No"),
    ]

    DEFAULT_CSS = """
    ConfirmModal { align: center middle; }
    ConfirmModal #confirm-box { width: 64; height: auto; padding: 1 2; border: round $warning; }
    """

    def __init__(self, prompt: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            # Prompt carries user-controlled text (session titles) — no markup.
            yield Static(self._prompt, markup=False)
            yield Static("\n[b]y[/] / Enter = yes    [b]n[/] / Esc = no")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

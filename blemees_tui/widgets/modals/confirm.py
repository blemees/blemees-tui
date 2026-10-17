"""Yes/no confirm modal — guards destructive keys (e.g. Ctrl+Shift+W delete)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class ConfirmModal(ModalScreen[bool]):
    """Dismisses with ``True`` on y, ``False`` on n / Esc / Enter (default is No)."""

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "No"),
        ("enter", "cancel", "No"),
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
            yield Static("\n[b]y[/] = yes    [b]n[/] / Esc / Enter = no")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

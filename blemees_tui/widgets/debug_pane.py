"""Raw-frame debug pane (spec §14.4). Stub."""

from __future__ import annotations

from collections import deque

from rich.markup import escape as rich_escape
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static


class DebugPane(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Close")]
    CAPACITY = 200

    DEFAULT_CSS = """
    DebugPane { align: center middle; }
    DebugPane #debug-frames { width: 90%; height: 80%; border: round $accent; }
    """

    def __init__(self, frames: deque[tuple[str, dict]] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._frames = frames or deque(maxlen=self.CAPACITY)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="debug-frames"):
            for direction, frame in self._frames:
                yield Static(f"[dim]{direction}[/]  {rich_escape(repr(frame))}")

    def action_dismiss(self) -> None:
        self.app.pop_screen()

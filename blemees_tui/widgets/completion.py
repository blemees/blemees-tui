"""Completion popup that surfaces ``:`` TUI commands and ``/`` skills.

Sits docked above the composer. Hidden by default; the app shows it when
the composer's first character is a known prefix and there's at least
one match. Selection inserts the chosen label into the composer.

Sources:

* ``:`` — TUI commands defined in ``blemees_tui.commands``.
* ``/`` — Claude Code built-ins + filesystem-discovered skills (see
  ``blemees_tui.discover``). Future: enrich with parsed ``/context``
  output so MCP servers and active agents show up too.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from ..commands import (
    _KNOWN as TUI_KNOWN,  # noqa: SLF001 — single-source-of-truth
    PREFIX as TUI_PREFIX,
)
from ..discover import Suggestion, filter_suggestions, slash_suggestions

_MAX_ROWS = 8


def _tui_suggestions() -> list[Suggestion]:
    return sorted(
        [
            Suggestion(label=f"{TUI_PREFIX}{name}", description="", source="tui")
            for name in TUI_KNOWN
        ],
        key=lambda s: s.label,
    )


class CompletionPopup(Vertical):
    DEFAULT_CSS = """
    CompletionPopup {
        width: 100%;
        height: auto;
        max-height: 10;
        background: $surface;
        border-top: tall $accent;
        border-bottom: tall $accent;
        padding: 0 1;
    }
    CompletionPopup.-hidden { display: none; }
    CompletionPopup #rows { height: auto; }
    CompletionPopup .row { height: 1; padding: 0 1; }
    CompletionPopup .row.-selected { background: $accent; color: $text; }
    CompletionPopup .desc { color: $text-muted; }
    """

    class Accepted(Message):
        def __init__(self, label: str) -> None:
            super().__init__()
            self.label = label

    class Dismissed(Message):
        pass

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.add_class("-hidden")
        self._all_slash: list[Suggestion] = []
        self._all_tui: list[Suggestion] = _tui_suggestions()
        self._matches: list[Suggestion] = []
        self._selected: int = 0
        self._row_widgets: list[Static] = []

    def compose(self) -> ComposeResult:
        yield Vertical(id="rows")

    def refresh_for(self, typed: str) -> None:
        """Update the popup based on the composer's current text."""
        if not typed:
            self._hide()
            return
        first = typed[0]
        if first == "/":
            pool = self._slash_pool()
        elif first == TUI_PREFIX:
            pool = self._all_tui
        else:
            self._hide()
            return
        self._matches = filter_suggestions(pool, typed)[:_MAX_ROWS]
        if not self._matches:
            self._hide()
            return
        self._selected = 0
        self._repaint()
        self._show()

    def _slash_pool(self) -> list[Suggestion]:
        if not self._all_slash:
            # Lazy first call — filesystem walks are cheap (<5ms typically)
            # but we still defer until needed.
            self._all_slash = slash_suggestions()
        return self._all_slash

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def _repaint(self) -> None:
        rows = self.query_one("#rows", Vertical)
        rows.remove_children()
        self._row_widgets = []
        for idx, item in enumerate(self._matches):
            text = self._format_row(item, selected=(idx == self._selected))
            row = Static(text, classes="row")
            if idx == self._selected:
                row.add_class("-selected")
            rows.mount(row)
            self._row_widgets.append(row)

    def _format_row(self, item: Suggestion, *, selected: bool) -> str:
        marker = "▸" if selected else " "
        source_tag = f"[dim]({item.source})[/]" if item.source else ""
        desc = f"[dim]— {item.description}[/]" if item.description else ""
        return f"{marker} [b]{item.label}[/] {source_tag} {desc}"

    # ------------------------------------------------------------------
    # Public interaction (called by the composer when the user presses
    # arrows / Tab / Enter / Esc and the popup is visible).
    # ------------------------------------------------------------------

    def is_visible(self) -> bool:
        return not self.has_class("-hidden") and bool(self._matches)

    def step(self, direction: int) -> None:
        if not self._matches:
            return
        self._selected = (self._selected + direction) % len(self._matches)
        self._repaint()

    def accept(self) -> None:
        if not self._matches:
            return
        chosen = self._matches[self._selected]
        self._hide()
        self.post_message(self.Accepted(chosen.label))

    def dismiss(self) -> None:
        if not self._matches:
            return
        self._hide()
        self.post_message(self.Dismissed())

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    def _show(self) -> None:
        self.remove_class("-hidden")

    def _hide(self) -> None:
        self.add_class("-hidden")
        self._matches = []

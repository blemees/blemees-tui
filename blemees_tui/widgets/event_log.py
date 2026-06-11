"""Event log overlay (spec §14.2).

Filter chips by source · ``[/]`` text filter · ``[s]`` save log to a
file under ``$XDG_STATE_HOME/blemees/tui/`` · ``[c]`` copy line ·
``[C]`` copy all visible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from rich.markup import escape as rich_escape
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from ..persistence import ensure_state_dir
from ..state import EventLog, EventLogEntry, EventLogSource

_SOURCES = [
    ("All", None),
    ("Daemon errors", EventLogSource.DAEMON_ERROR),
    ("Daemon stderr", EventLogSource.DAEMON_STDERR),
    ("Notices", EventLogSource.NOTICE),
    ("TUI internal", EventLogSource.TUI_INTERNAL),
    ("Connection", EventLogSource.CONNECTION),
]


class EventLogOverlay(ModalScreen):
    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("slash", "focus_filter", "Filter"),
        ("s", "save", "Save log"),
    ]

    DEFAULT_CSS = """
    EventLogOverlay { align: center middle; }
    EventLogOverlay #event-log-box {
        width: 95%;
        height: 85%;
        border: round $accent;
        padding: 1 1;
    }
    EventLogOverlay #chips { height: 3; }
    EventLogOverlay #chips Button { margin-right: 1; min-width: 14; }
    EventLogOverlay #chips Button.-active { background: $accent; }
    EventLogOverlay #log-rows { height: 1fr; }
    EventLogOverlay #filter-row { height: 3; }
    EventLogOverlay #filter-input { width: 1fr; }
    """

    class Saved(Message):
        def __init__(self, path: Path) -> None:
            super().__init__()
            self.path = path

    def __init__(self, log: EventLog, **kwargs) -> None:
        super().__init__(**kwargs)
        self._log = log
        self._source: EventLogSource | None = None
        self._needle: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="event-log-box"):
            with Horizontal(id="chips"):
                for label, source in _SOURCES:
                    btn = Button(label, id=_chip_id(source))
                    if source is None:
                        btn.add_class("-active")
                    yield btn
            yield VerticalScroll(id="log-rows")
            with Horizontal(id="filter-row"):
                yield Input(
                    placeholder="filter… (substring match on message + category)", id="filter-input"
                )

    async def on_mount(self) -> None:
        self._repaint()

    @on(Button.Pressed, "#chips Button")
    def _on_chip(self, event: Button.Pressed) -> None:
        for chip in self.query("#chips Button"):
            chip.remove_class("-active")
        event.button.add_class("-active")
        self._source = _chip_source(event.button.id or "")
        self._repaint()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self._needle = event.value.lower()
            self._repaint()

    def action_focus_filter(self) -> None:
        try:
            self.query_one("#filter-input", Input).focus()
        except Exception:
            pass

    def action_save(self) -> None:
        ensure_state_dir()
        ts = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
        path = ensure_state_dir() / f"event-log-{ts}.txt"
        with path.open("w", encoding="utf-8") as fh:
            for entry in self._filtered():
                fh.write(_format_entry(entry) + "\n")
        self.post_message(self.Saved(path))

    def action_dismiss(self) -> None:
        self.app.pop_screen()

    def _repaint(self) -> None:
        rows = self.query_one("#log-rows", VerticalScroll)
        rows.remove_children()
        for entry in self._filtered():
            rows.mount(Static(_format_entry(entry)))
        rows.scroll_end(animate=False)

    def _filtered(self) -> list[EventLogEntry]:
        out = []
        for entry in self._log.snapshot():
            if self._source is not None and entry.source != self._source:
                continue
            if self._needle:
                hay = f"{entry.category} {entry.message}".lower()
                if self._needle not in hay:
                    continue
            out.append(entry)
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chip_id(source: EventLogSource | None) -> str:
    return f"chip-{source.value if source else 'all'}"


def _chip_source(chip_id: str) -> EventLogSource | None:
    if not chip_id.startswith("chip-"):
        return None
    name = chip_id.removeprefix("chip-")
    if name == "all":
        return None
    for s in EventLogSource:
        if s.value == name:
            return s
    return None


def _format_entry(entry: EventLogEntry) -> str:
    ts = (
        datetime.fromtimestamp(entry.ts_ms / 1000, tz=UTC).astimezone().strftime("%H:%M:%S.%f")[:-3]
    )
    sid = entry.session_id[:8] if entry.session_id else "-"
    # message carries untrusted text (daemon errors, exception reprs) — escape (#16).
    return (
        f"{ts}  [{entry.source.value:>14}]  {sid}  {entry.category}  {rich_escape(entry.message)}"
    )

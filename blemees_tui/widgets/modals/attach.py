"""Attach (watch) modal — live-session picker (spec §8.1).

Two paths:
1. Pick from list — populated via ``agent.list_sessions{live:true}``.
2. Paste id — UUID-validated free-text fallback.

The modal does not own the connection; the app passes a coroutine
``fetch_live_sessions`` that resolves to the SessionSummary array.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class AttachModal(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Cancel")]

    DEFAULT_CSS = """
    AttachModal { align: center middle; }
    AttachModal #attach-box { width: 100; height: 28; padding: 1 2; border: round $accent; }
    AttachModal DataTable { height: 16; }
    AttachModal #paste-row { height: 3; }
    """

    class Submit(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    def __init__(
        self,
        fetch_live_sessions: Callable[[], Awaitable[list[dict[str, Any]]]],
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._fetch = fetch_live_sessions
        self._rows: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="attach-box"):
            yield Label("[b]Attach to live session[/b]")
            table = DataTable(id="live-sessions", zebra_stripes=True, cursor_type="row")
            table.add_columns("session", "backend", "cwd", "title", "owner pid", "started", "last active")
            yield table
            yield Label("[dim]Or paste a session id (UUID):[/]")
            yield Input(placeholder="00000000-0000-…", id="session-id")
            yield Button("Watch", id="submit", variant="primary")

    async def on_mount(self) -> None:
        await self._reload_table()

    async def _reload_table(self) -> None:
        table = self.query_one("#live-sessions", DataTable)
        table.clear()
        try:
            self._rows = await self._fetch()
        except Exception as exc:  # noqa: BLE001 — surface in a label, not a crash
            self.query_one("#attach-box", Vertical).mount(
                Label(f"[red]list_sessions failed: {exc}[/]")
            )
            return
        for row in self._rows:
            sid = str(row.get("session_id", ""))
            table.add_row(
                sid[:8],
                str(row.get("backend", "")),
                str(row.get("cwd", "") or ""),
                str(row.get("title", "") or "—"),
                str(row.get("owner_pid", "") or "—"),
                _fmt_ms(row.get("started_at_ms")),
                _fmt_ms(row.get("last_active_at_ms")),
                key=sid,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        sid = str(event.row_key.value)
        if sid:
            self.app.pop_screen()
            self.app.post_message(self.Submit(sid))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "submit":
            return
        sid = self.query_one("#session-id", Input).value.strip()
        if not _UUID_RE.match(sid):
            return
        self.app.pop_screen()
        self.app.post_message(self.Submit(sid))

    def action_dismiss(self) -> None:
        self.app.pop_screen()


def _fmt_ms(ms: Any) -> str:
    if not isinstance(ms, int) or ms <= 0:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone().strftime("%H:%M:%S")

"""Attach modal — registry-backed session picker (blemees/3, #3).

Two paths:
1. Pick from list — populated via ``session.list`` (the daemon registry,
   across profiles; not a disk scan).
2. Paste id — UUID-validated free-text fallback.

Attach as **viewer** (read-only) or **owner** (drives turns; takes over from
any current owner). The modal does not own the connection; the app passes a
coroutine ``fetch_sessions`` that resolves to the session rows.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
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
    AttachModal #attach-buttons { height: 3; }
    AttachModal #attach-buttons Button { margin-right: 2; }
    """

    class Submit(Message):
        def __init__(self, session_id: str, as_role: str = "viewer") -> None:
            super().__init__()
            self.session_id = session_id
            self.as_role = as_role  # "viewer" | "owner"

    def __init__(
        self,
        fetch_sessions: Callable[[], Awaitable[list[dict[str, Any]]]],
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._fetch = fetch_sessions
        self._rows: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="attach-box"):
            yield Label(
                "[b]Attach to a session[/b]  [dim](Enter / Watch = viewer · Take over = owner)[/]"
            )
            table = DataTable(id="live-sessions", zebra_stripes=True, cursor_type="row")
            table.add_columns("session", "profile", "state", "!", "cwd", "title", "last active")
            yield table
            yield Label("[dim]Or paste a session id (UUID):[/]")
            yield Input(placeholder="00000000-0000-…", id="session-id")
            with Horizontal(id="attach-buttons"):
                yield Button("Watch (viewer)", id="watch", variant="primary")
                yield Button("Take over (owner)", id="takeover", variant="warning")

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
                str(row.get("profile", "") or "—"),
                _state_label(row),
                "●" if row.get("needs_attention") else "",
                str(row.get("cwd", "") or ""),
                str(row.get("title", "") or "—"),
                _fmt_ms(row.get("last_active_at_ms")),
                key=sid,
            )

    def _selected_sid(self) -> str:
        """The pasted UUID if valid, else the highlighted table row's id."""
        typed = self.query_one("#session-id", Input).value.strip()
        if _UUID_RE.match(typed):
            return typed
        table = self.query_one("#live-sessions", DataTable)
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            return str(row_key.value or "")
        except Exception:
            return ""

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Row-activate = attach as viewer (the safe default).
        sid = str(event.row_key.value)
        if sid:
            self._submit(sid, "viewer")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        role = "owner" if event.button.id == "takeover" else "viewer"
        sid = self._selected_sid()
        if sid:
            self._submit(sid, role)

    def _submit(self, sid: str, as_role: str) -> None:
        self.app.pop_screen()
        self.app.post_message(self.Submit(sid, as_role))

    def action_dismiss(self) -> None:
        self.app.pop_screen()


def _state_label(row: dict[str, Any]) -> str:
    if row.get("view_only"):
        return "view-only"
    if row.get("attached"):
        return "attached"
    if row.get("running"):
        return "running"
    return "idle"


def _fmt_ms(ms: Any) -> str:
    if not isinstance(ms, int) or ms <= 0:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone().strftime("%H:%M:%S")

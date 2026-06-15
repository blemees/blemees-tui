"""New-session modal (blemees/3, #5).

Opens a session: the profile is fixed at TUI launch (``--profile``), so this
dialog only picks an **agent** within that profile, a cwd, and a title.
Profile/agent management lives in a separate dialog
(:class:`~blemees_tui.widgets.modals.agent_editor.AgentEditorModal`). Profiles
are loaded from ``profile.list`` (the daemon registry) via a fetch callback the
app supplies.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet


class NewSessionModal(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Cancel")]

    DEFAULT_CSS = """
    NewSessionModal { align: center middle; }
    NewSessionModal #new-session-box {
        width: 84;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
    }
    NewSessionModal Input { margin-bottom: 1; }
    NewSessionModal #open-buttons { height: 3; }
    NewSessionModal #open-buttons Button { margin-right: 2; }
    """

    class OpenSession(Message):
        def __init__(self, profile: str, agent: str, cwd: str, title: str) -> None:
            super().__init__()
            self.profile = profile
            self.agent = agent
            self.cwd = cwd
            self.title = title

    def __init__(
        self,
        fetch_profiles: Callable[[], Awaitable[list[dict[str, Any]]]],
        default_cwd: str = "",
        profile: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._fetch = fetch_profiles
        self._default_cwd = default_cwd
        # The active profile is fixed at TUI launch (``--profile``); the modal
        # never offers a profile choice. When blank, ``_reload`` adopts the
        # first profile the daemon reports.
        self._profile = profile
        self._profiles: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="new-session-box"):
            yield Label("[b]New session[/b]  [dim]pick an agent, then Open[/]")
            yield Label("", id="profile-name")
            yield Label("agent:")
            yield RadioSet(id="agents")
            yield Label("cwd:")
            yield Input(value=self._default_cwd, id="open-cwd")
            yield Label("title (optional):")
            yield Input(placeholder="architect", id="title")
            with Horizontal(id="open-buttons"):
                yield Button("Open", id="open", variant="primary")

    async def on_mount(self) -> None:
        await self._reload()

    async def _reload(self) -> None:
        radio = self.query_one("#agents", RadioSet)
        radio.remove_children()
        try:
            self._profiles = await self._fetch()
        except Exception as exc:  # noqa: BLE001 — surface, don't crash
            self.query_one("#new-session-box", VerticalScroll).mount(
                Label(f"[red]profile.list failed: {escape(str(exc))}[/]")
            )
            self._profiles = []
        # Resolve the active profile: the one fixed at launch, else the first
        # the daemon reports.
        row = next((r for r in self._profiles if str(r.get("name")) == self._profile), None)
        if row is None and self._profiles:
            row = self._profiles[0]
        if row is not None:
            self._profile = str(row.get("name", ""))
        self.query_one("#profile-name", Label).update(
            f"[dim]profile:[/] [b]{escape(self._profile) or '(none)'}[/]"
        )
        agents = (row.get("agents") if row else None) or []
        for ag in agents:
            if not isinstance(ag, dict):
                continue
            name = str(ag.get("name", ""))
            if not name:
                continue
            model = ag.get("model")
            # Escape daemon-supplied strings — names/models can contain markup
            # metacharacters that would otherwise corrupt the label (#34).
            label = f"{escape(name)}  [dim]{escape(str(model))}[/]" if model else escape(name)
            radio.mount(RadioButton(label, id=f"agent-{name}"))
        # Default-select the first agent. Setting ``value`` after the button is
        # mounted is what registers it as the RadioSet's pressed button (the
        # constructor ``value=`` does not when mounting dynamically).
        first = radio.query(RadioButton).first()
        if first is not None:
            first.value = True

    def _selected_agent(self) -> str:
        radio = self.query_one("#agents", RadioSet)
        btn = radio.pressed_button
        if btn is None or not btn.id:
            return ""
        return btn.id.removeprefix("agent-")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if (event.button.id or "") == "open":
            self._do_open()

    def _do_open(self) -> None:
        agent = self._selected_agent()
        if not self._profile or not agent:
            return
        cwd = self.query_one("#open-cwd", Input).value
        title = self.query_one("#title", Input).value
        self.app.pop_screen()
        self.app.post_message(self.OpenSession(self._profile, agent, cwd, title))

    def action_dismiss(self) -> None:
        self.app.pop_screen()

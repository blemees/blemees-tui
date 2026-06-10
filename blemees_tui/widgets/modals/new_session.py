"""New-session + profile-management modal (blemees/3, #5).

Profile-aware: pick an existing profile and open a session under it, or
create / edit / delete a profile via the over-wire profile CRUD (#25). The
editor exposes the agent binary/args, model, cwd, permission policy, MCP
servers, and notify webhook. Profiles are loaded from ``profile.list`` (the
daemon registry) via a fetch callback the app supplies.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Input, Label, RadioButton, RadioSet

_NEW = "__new__"  # the "create a new profile" radio row


def build_profile_spec(
    *,
    agent_command: str = "",
    args: str = "",
    model: str = "",
    cwd: str = "",
    permission_mode: str = "",
    detached: str = "",
    mcp_servers: str = "",
    notify_webhook: str = "",
) -> dict[str, Any]:
    """Build a profile spec (the ``profile`` object for profile.create/update)
    from the editor fields. Blank fields are omitted so the daemon keeps its
    defaults. ``args`` is whitespace-split; ``mcp_servers`` is a JSON array.

    Raises ``ValueError`` if mcp_servers isn't valid JSON.
    """
    agent: dict[str, Any] = {}
    if agent_command.strip():
        agent["agent_command"] = agent_command.strip()
    if args.strip():
        agent["args"] = args.split()
    if model.strip():
        agent["model"] = model.strip()
    if cwd.strip():
        agent["cwd"] = cwd.strip()
    if mcp_servers.strip():
        parsed = json.loads(mcp_servers)
        if not isinstance(parsed, list):
            raise ValueError("mcp_servers must be a JSON array")
        agent["mcp_servers"] = parsed

    spec: dict[str, Any] = {"agent": agent}
    policy: dict[str, Any] = {}
    if permission_mode.strip():
        policy["mode"] = permission_mode.strip()
    if detached.strip():
        policy["detached"] = detached.strip()
    if policy:
        spec["permission_policy"] = policy
    if notify_webhook.strip():
        spec["notify"] = {"webhook_url": notify_webhook.strip()}
    return spec


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
    NewSessionModal Collapsible { margin-top: 1; }
    NewSessionModal Input { margin-bottom: 1; }
    NewSessionModal #open-buttons { height: 3; }
    NewSessionModal #open-buttons Button { margin-right: 2; }
    """

    class OpenSession(Message):
        def __init__(self, profile: str, cwd: str, title: str) -> None:
            super().__init__()
            self.profile = profile
            self.cwd = cwd
            self.title = title

    class SaveProfile(Message):
        def __init__(self, name: str, spec: dict[str, Any]) -> None:
            super().__init__()
            self.name = name
            self.spec = spec

    class DeleteProfile(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(
        self,
        fetch_profiles: Callable[[], Awaitable[list[dict[str, Any]]]],
        default_cwd: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._fetch = fetch_profiles
        self._default_cwd = default_cwd
        self._profiles: list[dict[str, Any]] = []
        # Radio-row position → profile name (last row is the _NEW sentinel).
        # Names never go into widget ids — the daemon's charset is wider than
        # Textual's identifier rules, so a name like "my.profile" used as an
        # id crashes the app with BadIdentifier (#23).
        self._radio_names: list[str] = []

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="new-session-box"):
            yield Label("[b]New session[/b]  [dim]pick a profile, then Open[/]")
            yield RadioSet(id="profiles")
            yield Label("cwd:")
            yield Input(value=self._default_cwd, id="open-cwd")
            yield Label("title (optional):")
            yield Input(placeholder="architect", id="title")
            with Horizontal(id="open-buttons"):
                yield Button("Open", id="open", variant="primary")
                yield Button("Delete profile", id="delete", variant="error")
            with Collapsible(title="Create / edit profile", collapsed=True, id="editor"):
                yield Label("name:")
                yield Input(placeholder="claude-sonnet", id="p-name")
                yield Label("agent command:")
                yield Input(placeholder="claude-agent-acp", id="p-agent_command")
                yield Label("args (space-separated):")
                yield Input(placeholder="acp", id="p-args")
                yield Label("model:")
                yield Input(placeholder="sonnet", id="p-model")
                yield Label("default cwd:")
                yield Input(id="p-cwd")
                yield Label("permission mode (relay · allow · deny):")
                yield Input(placeholder="relay", id="p-permission_mode")
                yield Label("detached (stall · allow · deny):")
                yield Input(placeholder="stall", id="p-detached")
                yield Label("mcp_servers (JSON array):")
                yield Input(placeholder="[]", id="p-mcp")
                yield Label("notify webhook URL:")
                yield Input(id="p-notify_webhook")
                yield Button("Save profile", id="save", variant="success")

    async def on_mount(self) -> None:
        await self._reload()

    async def _reload(self) -> None:
        radio = self.query_one("#profiles", RadioSet)
        radio.remove_children()
        try:
            self._profiles = await self._fetch()
        except Exception as exc:  # noqa: BLE001 — surface, don't crash
            self.query_one("#new-session-box", VerticalScroll).mount(
                Label(f"[red]profile.list failed: {exc}[/]")
            )
            self._profiles = []
        self._radio_names = []
        for idx, row in enumerate(self._profiles):
            name = str(row.get("name", ""))
            source = str(row.get("source", ""))
            label = f"{escape(name)}  [dim]{escape(source)}[/]" if source else escape(name)
            self._radio_names.append(name)
            radio.mount(RadioButton(label, value=(idx == 0), id=f"profile-{idx}"))
        radio.mount(RadioButton("＋ New profile…", id=f"profile-{len(self._radio_names)}"))
        self._radio_names.append(_NEW)

    def _radio_name(self, widget_id: str | None) -> str:
        """Map a radio row's index-based widget id back to its profile name."""
        idx_str = (widget_id or "").removeprefix("profile-")
        try:
            return self._radio_names[int(idx_str)]
        except (ValueError, IndexError):
            return ""

    def _selected_profile(self) -> str:
        radio = self.query_one("#profiles", RadioSet)
        btn = radio.pressed_button
        if btn is None or not btn.id:
            return ""
        return self._radio_name(btn.id)

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        # Pre-fill the editor from the selected profile's known fields; expand
        # the editor when "New profile" is chosen.
        name = self._radio_name(event.pressed.id)
        editor = self.query_one("#editor", Collapsible)
        if name == _NEW:
            self.query_one("#p-name", Input).value = ""
            self.query_one("#p-agent_command", Input).value = ""
            self.query_one("#p-model", Input).value = ""
            editor.collapsed = False
            return
        row = next((r for r in self._profiles if str(r.get("name")) == name), None)
        if row is None:
            return
        self.query_one("#p-name", Input).value = name
        agents = row.get("agents") or []
        first = agents[0] if agents and isinstance(agents[0], dict) else {}
        self.query_one("#p-agent_command", Input).value = str(first.get("agent", "") or "")
        self.query_one("#p-model", Input).value = str(first.get("model", "") or "")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "open":
            self._do_open()
        elif bid == "delete":
            self._do_delete()
        elif bid == "save":
            self._do_save()

    def _do_open(self) -> None:
        profile = self._selected_profile()
        if not profile or profile == _NEW:
            return
        cwd = self.query_one("#open-cwd", Input).value
        title = self.query_one("#title", Input).value
        self.app.pop_screen()
        self.app.post_message(self.OpenSession(profile, cwd, title))

    def _do_delete(self) -> None:
        profile = self._selected_profile()
        if not profile or profile == _NEW:
            return
        self.app.pop_screen()
        self.app.post_message(self.DeleteProfile(profile))

    def _do_save(self) -> None:
        name = self.query_one("#p-name", Input).value.strip()
        if not name:
            return
        try:
            spec = build_profile_spec(
                agent_command=self.query_one("#p-agent_command", Input).value,
                args=self.query_one("#p-args", Input).value,
                model=self.query_one("#p-model", Input).value,
                cwd=self.query_one("#p-cwd", Input).value,
                permission_mode=self.query_one("#p-permission_mode", Input).value,
                detached=self.query_one("#p-detached", Input).value,
                mcp_servers=self.query_one("#p-mcp", Input).value,
                notify_webhook=self.query_one("#p-notify_webhook", Input).value,
            )
        except ValueError:
            self.query_one("#new-session-box", VerticalScroll).mount(
                Label("[red]mcp_servers must be a JSON array[/]")
            )
            return
        self.app.pop_screen()
        self.app.post_message(self.SaveProfile(name, spec))

    def action_dismiss(self) -> None:
        self.app.pop_screen()

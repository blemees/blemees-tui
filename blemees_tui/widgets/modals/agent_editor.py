"""Agent-editor modal — define an agent and add it to a profile (#5/#25).

Separate from :class:`~blemees_tui.widgets.modals.new_session.NewSessionModal`
(which only opens sessions). This dialog manages a profile's agents over the
wire (``profile.create`` / ``profile.update``).

Two constraints, surfaced by the app when a save is rejected:

* ``profile.update`` is a *full replace* and ``profile.list`` returns only
  agent summaries (name/model), so this dialog defines a profile as the single
  agent entered here — it does not merge into an existing rich agent set.
* Config-managed profiles (those from ``agentd.toml``, e.g. ``blemees``) are
  protected from over-wire edits; add agents to those by editing the config
  file. This dialog is for *dynamic* profiles.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label


def build_profile_spec(
    *,
    agent_name: str = "",
    agent_command: str = "",
    args: str = "",
    model: str = "",
    agent_home: str = "",
    env: str = "",
    permission_mode: str = "",
    detached: str = "",
    mcp_servers: str = "",
    notify_webhook: str = "",
) -> dict[str, Any]:
    """Build a profile spec (the ``profile`` object for profile.create/update).

    Blank fields are omitted so the daemon keeps its defaults. ``args`` is
    whitespace-split; ``mcp_servers`` is a JSON array; ``env`` is a JSON object
    of string→string. When ``agent_name`` is given the agent is placed under an
    ``agents`` table keyed by that name; otherwise it becomes the profile's
    single ``agent``.

    Raises ``ValueError`` if ``mcp_servers``/``env`` aren't valid JSON of the
    expected shape.
    """
    agent: dict[str, Any] = {}
    if agent_command.strip():
        agent["agent_command"] = agent_command.strip()
    if args.strip():
        agent["args"] = args.split()
    if model.strip():
        agent["model"] = model.strip()
    if agent_home.strip():
        agent["agent_home"] = agent_home.strip()
    if mcp_servers.strip():
        parsed = json.loads(mcp_servers)
        if not isinstance(parsed, list):
            raise ValueError("mcp_servers must be a JSON array")
        agent["mcp_servers"] = parsed
    if env.strip():
        parsed_env = json.loads(env)
        if not isinstance(parsed_env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in parsed_env.items()
        ):
            raise ValueError("env must be a JSON object of string→string")
        agent["env"] = parsed_env

    name = agent_name.strip()
    spec: dict[str, Any] = {"agents": {name: agent}} if name else {"agent": agent}
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


class AgentEditorModal(ModalScreen):
    """Define an agent and save it onto a profile."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    DEFAULT_CSS = """
    AgentEditorModal { align: center middle; }
    AgentEditorModal #agent-editor-box {
        width: 84;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
    }
    AgentEditorModal Input { margin-bottom: 1; }
    AgentEditorModal #agent-editor-buttons { height: 3; }
    AgentEditorModal #agent-editor-buttons Button { margin-right: 2; }
    """

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
        default_profile: str = "",
        fetch_profiles: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._default_profile = default_profile
        self._fetch = fetch_profiles

    def compose(self) -> ComposeResult:
        with Vertical(id="agent-editor-box"):
            yield Label("[b]Add agent to profile[/b]")
            yield Label("profile:")
            yield Input(value=self._default_profile, placeholder="my-profile", id="p-name")
            yield Label("agent name:")
            yield Input(placeholder="developer", id="p-agent_name")
            yield Label("agent command:")
            yield Input(placeholder="claude-agent-acp", id="p-agent_command")
            yield Label("args (space-separated):")
            yield Input(placeholder="acp", id="p-args")
            yield Label("model:")
            yield Input(placeholder="sonnet", id="p-model")
            yield Label("agent home:")
            yield Input(id="p-agent_home")
            yield Label("env (JSON object):")
            yield Input(placeholder="{}", id="p-env")
            yield Label("permission mode (relay · allow · deny):")
            yield Input(placeholder="relay", id="p-permission_mode")
            yield Label("detached (stall · allow · deny):")
            yield Input(placeholder="stall", id="p-detached")
            yield Label("mcp_servers (JSON array):")
            yield Input(placeholder="[]", id="p-mcp")
            yield Label("notify webhook URL:")
            yield Input(id="p-notify_webhook")
            with Vertical(id="agent-editor-buttons"):
                yield Button("Save", id="save", variant="success")
                yield Button("Delete profile", id="delete", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "save":
            self._do_save()
        elif bid == "delete":
            self._do_delete()

    def _do_save(self) -> None:
        name = self.query_one("#p-name", Input).value.strip()
        if not name:
            return
        try:
            spec = build_profile_spec(
                agent_name=self.query_one("#p-agent_name", Input).value,
                agent_command=self.query_one("#p-agent_command", Input).value,
                args=self.query_one("#p-args", Input).value,
                model=self.query_one("#p-model", Input).value,
                agent_home=self.query_one("#p-agent_home", Input).value,
                env=self.query_one("#p-env", Input).value,
                permission_mode=self.query_one("#p-permission_mode", Input).value,
                detached=self.query_one("#p-detached", Input).value,
                mcp_servers=self.query_one("#p-mcp", Input).value,
                notify_webhook=self.query_one("#p-notify_webhook", Input).value,
            )
        except ValueError as exc:
            self.query_one("#agent-editor-box", Vertical).mount(Label(f"[red]{exc}[/]"))
            return
        self.app.pop_screen()
        self.app.post_message(self.SaveProfile(name, spec))

    def _do_delete(self) -> None:
        name = self.query_one("#p-name", Input).value.strip()
        if not name:
            return
        self.app.pop_screen()
        self.app.post_message(self.DeleteProfile(name))

    def action_dismiss(self) -> None:
        self.app.pop_screen()

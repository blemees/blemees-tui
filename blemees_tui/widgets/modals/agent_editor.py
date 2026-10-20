"""Agent-editor modal — define an agent and save it onto a profile (#5/#25/#12).

Separate from :class:`~blemees_tui.widgets.modals.new_session.NewSessionModal`
(which only opens sessions). This dialog manages a profile over the wire
(``profile.get`` / ``profile.create`` / ``profile.update``).

Two modes:

* **Create** (the profile name is new): the dialog defines a profile as the
  single agent entered here; blank fields are omitted so the daemon keeps its
  defaults (:func:`build_profile_spec`).
* **Edit** (the name resolves via ``profile.get``, #12): ``profile.update`` is
  a *full replace*, so the dialog pre-fills every field from the daemon's full
  spec (:func:`profile_fields`) and saves the fetched spec with the edited
  agent's fields overlaid (:func:`merge_profile_spec`) — other agents, the
  agent ``mode``, ``attention``, ``notify.format`` … are kept. Secrets come
  back as the literal ``"<redacted>"``; sent back untouched, the daemon keeps
  the stored value. A blanked field clears that value.

Config-managed profiles (those from ``agentd.toml``, e.g. ``blemees``) can be
loaded but the daemon refuses to update them (``profile_protected``); edit the
config file instead.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Awaitable, Callable
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

#: The daemon's placeholder for a secret value in ``profile.info`` (#12).
REDACTED = "<redacted>"


def _escape(text: str) -> str:
    """Escape every ``[`` for Textual's markup parser (daemon messages carry
    paths like ``mcp_servers[1]``)."""
    return text.replace("[", r"\[")


def _parse_env(env: str) -> dict[str, str]:
    parsed = json.loads(env)
    if not isinstance(parsed, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
    ):
        raise ValueError("env must be a JSON object of string→string")
    return parsed


def _parse_mcp(mcp_servers: str) -> list[Any]:
    parsed = json.loads(mcp_servers)
    if not isinstance(parsed, list):
        raise ValueError("mcp_servers must be a JSON array")
    return parsed


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
        agent["mcp_servers"] = _parse_mcp(mcp_servers)
    if env.strip():
        agent["env"] = _parse_env(env)

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


#: The editor's text fields, in the order of :func:`build_profile_spec`'s
#: keywords. :func:`profile_fields` returns exactly these keys.
FIELDS = (
    "agent_name",
    "agent_command",
    "args",
    "model",
    "agent_home",
    "env",
    "permission_mode",
    "detached",
    "mcp_servers",
    "notify_webhook",
)


def _agent_args(agent: dict[str, Any]) -> list[Any]:
    # The daemon reads ``agent_args`` ahead of ``args``.
    args = agent.get("agent_args", agent.get("args"))
    return list(args) if isinstance(args, list) else []


def profile_fields(spec: dict[str, Any], agent: str | None = None) -> dict[str, str]:
    """Editor field values for one agent of a ``profile.get`` spec (#12).

    ``agent`` picks the agent; when ``None`` or unknown, the first one in the
    ``agents`` table is used (``agent_name`` names the one chosen, ``""`` when
    the profile has none). ``args`` is space-joined; ``env`` and
    ``mcp_servers`` are compact JSON (blank when empty). ``"<redacted>"``
    values are returned as-is.
    """
    agents = spec.get("agents")
    agents = agents if isinstance(agents, dict) else {}
    name = agent if agent in agents else next(iter(agents), "")
    entry = agents.get(name)
    entry = entry if isinstance(entry, dict) else {}
    policy = spec.get("permission_policy")
    policy = policy if isinstance(policy, dict) else {}
    notify = spec.get("notify")
    notify = notify if isinstance(notify, dict) else {}
    env = entry.get("env") or {}
    mcp = entry.get("mcp_servers") or []

    def _s(value: Any) -> str:
        return "" if value is None else str(value)

    return {
        "agent_name": name,
        # The daemon reads ``agent_command`` ahead of ``command``.
        "agent_command": _s(entry.get("agent_command") or entry.get("command")),
        "args": " ".join(str(a) for a in _agent_args(entry)),
        "model": _s(entry.get("model")),
        "agent_home": _s(entry.get("agent_home")),
        "env": json.dumps(env) if env else "",
        "permission_mode": _s(policy.get("mode")),
        "detached": _s(policy.get("detached")),
        "mcp_servers": json.dumps(mcp) if mcp else "",
        "notify_webhook": _s(notify.get("webhook_url")),
    }


def merge_profile_spec(
    base: dict[str, Any], *, agent: str | None = None, **fields: str
) -> dict[str, Any]:
    """Overlay the editor's fields onto a fetched ``profile.get`` spec (#12).

    ``base`` is not modified. Only the edited agent (``agent``; the first one
    when ``None``) and the shown policy/notify keys change: other agents, the
    agent's ``mode``, ``attention``, ``notify.format`` and any other key are
    kept. A blank field *clears* its value (``args``/``env``/``mcp_servers``
    become empty; ``model``/``agent_home``/``command``/permission
    keys/``webhook_url`` are removed). ``"<redacted>"`` strings are sent back
    verbatim, so the daemon keeps the stored secret. A field left exactly as
    :func:`profile_fields` showed it keeps the stored value verbatim (so e.g.
    an arg containing a space survives the space-joined round trip).

    The command is written to ``command`` (``agent_command``/``agent_args``
    aliases are dropped). A changed ``agent_name`` renames the edited agent;
    renaming onto another existing agent, or while its ``env`` still holds a
    ``"<redacted>"`` (the daemon would drop that secret), raises
    ``ValueError``, as does invalid ``env``/``mcp_servers`` JSON.
    """
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise TypeError(f"unknown field(s): {', '.join(sorted(unknown))}")
    spec = copy.deepcopy(base)
    agents = spec.get("agents")
    agents = agents if isinstance(agents, dict) else {}
    shown = profile_fields(spec, agent)
    key = shown["agent_name"] or "default"
    entry = agents.get(key)
    entry = copy.deepcopy(entry) if isinstance(entry, dict) else {}
    vals = {f: fields.get(f, shown[f]) for f in FIELDS}

    def _changed(field: str) -> bool:
        return vals[field] != shown[field]

    args = _agent_args(entry)
    entry.pop("agent_command", None)
    entry.pop("agent_args", None)
    command = vals["agent_command"].strip()
    if command:
        entry["command"] = command
    else:
        entry.pop("command", None)
    entry["args"] = vals["args"].split() if _changed("args") else args
    for field in ("model", "agent_home"):
        value = vals[field].strip()
        if value:
            entry[field] = value
        else:
            entry.pop(field, None)
    if _changed("env"):
        entry["env"] = _parse_env(vals["env"]) if vals["env"].strip() else {}
    else:
        entry["env"] = entry.get("env") or {}
    if _changed("mcp_servers"):
        mcp = vals["mcp_servers"]
        entry["mcp_servers"] = _parse_mcp(mcp) if mcp.strip() else []
    else:
        entry["mcp_servers"] = entry.get("mcp_servers") or []

    new_key = vals["agent_name"].strip() or key
    if new_key != key and new_key in agents:
        raise ValueError(
            f"agent {new_key!r} already exists in this profile; "
            "press Enter in the agent name field to edit it"
        )
    if new_key != key and key in agents:
        # The daemon matches agents by name: an env placeholder under a name
        # it has nothing stored for is dropped, losing the secret silently.
        # (MCP placeholders it refuses outright, so they need no guard.)
        kept = sorted(k for k, v in entry["env"].items() if v == REDACTED)
        if kept:
            raise ValueError(
                f"renaming agent {key!r} to {new_key!r} would drop its stored env "
                f"secrets ({', '.join(kept)}): re-enter those values or keep the name"
            )
    # Rebuild the table so a rename keeps the agent's position.
    spec["agents"] = {
        (new_key if k == key else k): (entry if k == key else v) for k, v in agents.items()
    }
    if key not in agents:
        spec["agents"][new_key] = entry

    policy = spec.get("permission_policy")
    policy = dict(policy) if isinstance(policy, dict) else {}
    for field, wire in (("permission_mode", "mode"), ("detached", "detached")):
        value = vals[field].strip()
        if value:
            policy[wire] = value
        else:
            policy.pop(wire, None)
    spec["permission_policy"] = policy

    notify = spec.get("notify")
    notify = dict(notify) if isinstance(notify, dict) else {}
    webhook = vals["notify_webhook"].strip()
    if webhook:
        notify["webhook_url"] = webhook
    else:
        notify.pop("webhook_url", None)
    spec["notify"] = notify
    return spec


class AgentEditorModal(ModalScreen):
    """Define an agent and save it onto a profile.

    With a ``fetch_profile`` callback (``profile.get``), a known profile name
    (``default_profile`` on mount, or Enter in the profile field) switches the
    dialog into edit mode: every field is pre-filled and Save sends a
    ``profile.update`` built on the fetched spec. In edit mode the dialog stays
    open until the app reports the outcome — :meth:`show_save_error` keeps the
    user's edits so a refused secret can be re-entered.
    """

    BINDINGS = [("escape", "dismiss", "Cancel")]

    DEFAULT_CSS = """
    AgentEditorModal { align: center middle; }
    AgentEditorModal #agent-editor-box {
        width: 84;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
    }
    AgentEditorModal Input { margin-bottom: 1; }
    AgentEditorModal #agent-editor-buttons { height: 3; }
    AgentEditorModal #agent-editor-buttons Button { margin-right: 2; }
    """

    class SaveProfile(Message):
        """Save ``spec`` as profile ``name``. ``update`` (edit mode, #12) asks
        for ``profile.update`` directly; otherwise create, falling back to
        update when the profile exists."""

        def __init__(self, name: str, spec: dict[str, Any], *, update: bool = False) -> None:
            super().__init__()
            self.name = name
            self.spec = spec
            self.update = update

    class DeleteProfile(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(
        self,
        default_profile: str = "",
        fetch_profiles: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
        fetch_profile: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._default_profile = default_profile
        self._fetch = fetch_profiles
        self._fetch_one = fetch_profile
        # Edit state (#12): the fetched spec, its profile name/source and the
        # agent being edited. ``None`` base = create mode.
        self._base: dict[str, Any] | None = None
        self._loaded_name = ""
        self._source = ""
        self._edit_agent = ""

    @property
    def editing(self) -> bool:
        """True when Save will update the loaded profile (edit mode)."""
        return self._base is not None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="agent-editor-box"):
            yield Label("[b]Add agent to profile[/b]")
            yield Label("profile (Enter loads an existing one):")
            yield Input(value=self._default_profile, placeholder="my-profile", id="p-name")
            yield Label("", id="p-status")
            yield Label("", id="p-hint")
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
            yield Label("", id="p-error")
            with Vertical(id="agent-editor-buttons"):
                yield Button("Save", id="save", variant="success")
                yield Button("Delete profile", id="delete", variant="error")

    # Field name (build_profile_spec keyword) → Input id.
    _INPUTS = {
        "agent_name": "#p-agent_name",
        "agent_command": "#p-agent_command",
        "args": "#p-args",
        "model": "#p-model",
        "agent_home": "#p-agent_home",
        "env": "#p-env",
        "permission_mode": "#p-permission_mode",
        "detached": "#p-detached",
        "mcp_servers": "#p-mcp",
        "notify_webhook": "#p-notify_webhook",
    }

    async def on_mount(self) -> None:
        if self._default_profile:
            await self._load(self._default_profile)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "p-name":
            await self._load(event.value.strip())
        elif event.input.id == "p-agent_name" and self._base is not None:
            # Switch the edited agent when the name is one of the profile's.
            name = event.value.strip()
            if name in (self._base.get("agents") or {}) and name != self._edit_agent:
                self._edit_agent = name
                self._fill(profile_fields(self._base, name))
                self._show_edit_status()

    def on_input_changed(self, event: Input.Changed) -> None:
        # Renaming the profile away from the loaded one drops the edit state:
        # Save then creates a new profile, as before #12.
        if event.input.id == "p-name" and self._base is not None:
            if event.value.strip() != self._loaded_name:
                self._clear_edit_state()
                self._set_status("[dim]new profile — Save creates it[/]")

    def _fields(self) -> dict[str, str]:
        return {f: self.query_one(sel, Input).value for f, sel in self._INPUTS.items()}

    def _fill(self, values: dict[str, str]) -> None:
        for field, sel in self._INPUTS.items():
            self.query_one(sel, Input).value = values.get(field, "")

    def _set_status(self, markup: str) -> None:
        self.query_one("#p-status", Label).update(markup)

    def _set_error(self, text: str) -> None:
        self.query_one("#p-error", Label).update(f"[red]{_escape(text)}[/]" if text else "")

    def _clear_edit_state(self) -> None:
        self._base = None
        self._loaded_name = ""
        self._source = ""
        self._edit_agent = ""
        self.query_one("#p-hint", Label).update("")

    async def _load(self, name: str) -> None:
        """Fetch ``name`` via ``profile.get`` and enter edit mode (#12)."""
        if not name or self._fetch_one is None:
            return
        self._set_error("")
        try:
            reply = await self._fetch_one(name)
        except Exception as exc:  # noqa: BLE001 — surface, don't crash
            self._clear_edit_state()
            if "profile_unknown" in str(exc):
                self._set_status("[dim]new profile — Save creates it[/]")
            else:
                self._set_status(f"[red]profile.get failed: {_escape(str(exc))}[/]")
            return
        spec = reply.get("profile") if isinstance(reply, dict) else None
        if not isinstance(spec, dict):
            self._clear_edit_state()
            self._set_status("[red]profile.get returned no profile[/]")
            return
        self._base = copy.deepcopy(spec)
        self._loaded_name = name
        self._source = str(reply.get("source") or "")
        values = profile_fields(self._base)
        self._edit_agent = values["agent_name"]
        self._fill(values)
        self._show_edit_status()
        self.query_one("#p-hint", Label).update(
            f'[dim]"{REDACTED}" keeps the stored secret; replace it to change it.[/]'
        )

    def _show_edit_status(self) -> None:
        agents = list((self._base or {}).get("agents") or {})
        text = f"[b]editing[/] {_escape(self._loaded_name)}"
        if len(agents) > 1:
            listed = ", ".join(_escape(a) for a in agents)
            text += f"  [dim]agents: {listed} — editing[/] {_escape(self._edit_agent)}"
            text += "  [dim](Enter another name in agent name to switch)[/]"
        if self._source == "config":
            text += (
                "\n[$warning]config-managed (agentd.toml): the daemon will refuse"
                " an update — edit the config file instead[/]"
            )
        self._set_status(text)

    def show_save_error(self, message: str) -> None:
        """Show a refused save inline, keeping the user's edits (#12)."""
        self.query_one("#save", Button).disabled = False
        self._set_error(f"not saved: {message}")

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
        fields = self._fields()
        editing = self._base is not None and name == self._loaded_name
        try:
            if editing:
                spec = merge_profile_spec(self._base or {}, agent=self._edit_agent, **fields)
            else:
                spec = build_profile_spec(**fields)
        except ValueError as exc:
            self._set_error(str(exc))
            return
        if editing:
            # Stay open: the app pops the dialog on success, or calls
            # show_save_error so a refused placeholder can be re-entered.
            self._set_error("")
            self.query_one("#save", Button).disabled = True
            self.app.post_message(self.SaveProfile(name, spec, update=True))
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

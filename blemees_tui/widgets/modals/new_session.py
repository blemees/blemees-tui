"""New session modal (spec §7.1).

Backend pick · model · cwd · title · collapsible **Advanced** section
that builds the per-backend ``options.<backend>`` map.

The Advanced section is intentionally form-driven for the common knobs and
falls back to a free-text TOML/JSON box for the long tail (Codex
``config``; Claude ``agents`` / raw mcp). Inputs left empty are *not*
sent to the daemon — the backend keeps its default.
"""

from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Input, Label, RadioButton, RadioSet


class NewSessionModal(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Cancel")]

    DEFAULT_CSS = """
    NewSessionModal { align: center middle; }
    NewSessionModal #new-session-box {
        width: 80;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
    }
    NewSessionModal Collapsible { margin-top: 1; }
    NewSessionModal Input { margin-bottom: 1; }
    """

    class Submit(Message):
        def __init__(
            self,
            backend: str,
            model: str,
            cwd: str,
            title: str,
            options: dict[str, Any],
        ) -> None:
            super().__init__()
            self.backend = backend
            self.model = model
            self.cwd = cwd
            self.title = title
            self.options = options

    # Every backend the TUI knows how to drive. The radio always lists both;
    # rows for backends the daemon didn't advertise are disabled so the user
    # can see *why* they're stuck on one (rather than wondering if the
    # picker is broken).
    SUPPORTED_BACKENDS = ("claude", "codex")

    def __init__(self, available_backends: list[str], default_cwd: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._available = set(available_backends)
        self._default_cwd = default_cwd
        # Default to first available; fall back to first supported if none.
        first_available = next(
            (b for b in self.SUPPORTED_BACKENDS if b in self._available), None
        )
        self._default_backend = first_available or self.SUPPORTED_BACKENDS[0]

    def compose(self) -> ComposeResult:
        with Vertical(id="new-session-box"):
            yield Label("[b]New session[/b]")

            yield Label("Backend:  [green]●[/] detected · [dim]○ not detected[/]")
            with RadioSet(id="backend"):
                for name in self.SUPPORTED_BACKENDS:
                    detected = name in self._available
                    dot = "[green]●[/]" if detected else "[dim]○[/]"
                    suffix = "" if detected else " [dim](not detected by daemon)[/]"
                    btn = RadioButton(
                        f"{dot} {name}{suffix}",
                        value=(name == self._default_backend and detected),
                        id=f"backend-{name}",
                    )
                    if not detected:
                        btn.disabled = True
                    yield btn

            yield Label("Model:")
            yield Input(placeholder="sonnet · gpt-5.2-codex · opus · …", id="model")

            yield Label("cwd:")
            yield Input(value=self._default_cwd, id="cwd")

            yield Label("Title (optional):")
            yield Input(id="title")

            with Collapsible(title="Advanced — Claude", collapsed=True, id="adv-claude"):
                yield Label("permission_mode (default · acceptEdits · bypassPermissions · plan):")
                yield Input(placeholder="default", id="cc-permission_mode")
                yield Label("tools (comma-separated; blank = default, type 'none' to disable all):")
                yield Input(id="cc-tools")
                yield Label("disallowed_tools (comma-separated):")
                yield Input(id="cc-disallowed_tools")
                yield Label("system_prompt (overrides default):")
                yield Input(id="cc-system_prompt")
                yield Label("effort (e.g. high, medium, low):")
                yield Input(id="cc-effort")
                yield Label("agent (subagent name):")
                yield Input(id="cc-agent")
                yield Label("betas (comma-separated):")
                yield Input(id="cc-betas")
                yield Label("mcp_config paths (comma-separated):")
                yield Input(id="cc-mcp_config")

            with Collapsible(title="Advanced — Codex", collapsed=True, id="adv-codex"):
                yield Label("sandbox (read-only · workspace-write · danger-full-access):")
                yield Input(placeholder="workspace-write", id="cx-sandbox")
                yield Label("approval-policy (untrusted · on-failure · on-request · never):")
                yield Input(id="cx-approval_policy")
                yield Label("developer-instructions:")
                yield Input(id="cx-developer_instructions")
                yield Label("base-instructions:")
                yield Input(id="cx-base_instructions")
                yield Label("compact-prompt:")
                yield Input(id="cx-compact_prompt")
                yield Label("config (raw JSON merged into options.codex):")
                yield Input(placeholder='{"key":"value"}', id="cx-config")

            yield Button("Create", id="submit", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "submit":
            return
        radio = self.query_one("#backend", RadioSet)
        backend = ""
        if radio.pressed_button is not None and radio.pressed_button.id:
            backend = radio.pressed_button.id.removeprefix("backend-")
        if not backend or backend not in self._available:
            # Either nothing selected or the user managed to land on a
            # disabled row. Refuse to submit — the daemon would reject it
            # with `unknown_backend` anyway.
            return
        model = self.query_one("#model", Input).value
        cwd = self.query_one("#cwd", Input).value
        title = self.query_one("#title", Input).value
        options = self._collect_options(backend, model, cwd)
        self.app.pop_screen()
        self.app.post_message(self.Submit(backend, model, cwd, title, options))

    def _collect_options(self, backend: str, model: str, cwd: str) -> dict[str, Any]:
        opts: dict[str, Any] = {}
        if model:
            opts["model"] = model
        if cwd:
            opts["cwd"] = cwd
        if backend == "claude":
            opts.update(self._claude_options())
        elif backend == "codex":
            opts.update(self._codex_options())
        return opts

    def _claude_options(self) -> dict[str, Any]:
        # Note: only `tools` is documented as having empty-string semantics
        # ("" disables all tools, schema options.claude.json). Every other
        # field MUST be omitted when blank — sending "" crashes the backend
        # (e.g. `claude -p --permission-mode ""` fails arg validation).
        out: dict[str, Any] = {}
        for field, key, kind in (
            ("cc-permission_mode", "permission_mode", "str_nonempty"),
            ("cc-tools", "tools", "str_tools"),
            ("cc-disallowed_tools", "disallowed_tools", "list"),
            ("cc-system_prompt", "system_prompt", "str_nonempty"),
            ("cc-effort", "effort", "str_nonempty"),
            ("cc-agent", "agent", "str_nonempty"),
            ("cc-betas", "betas", "list"),
            ("cc-mcp_config", "mcp_config", "list"),
        ):
            try:
                value = self.query_one(f"#{field}", Input).value
            except Exception:
                continue
            coerced = _coerce_input(value, kind)
            if coerced is not _UNSET:
                out[key] = coerced
        return out

    def _codex_options(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field, key, kind in (
            ("cx-sandbox", "sandbox", "str_nonempty"),
            ("cx-approval_policy", "approval-policy", "str_nonempty"),
            ("cx-developer_instructions", "developer-instructions", "str_nonempty"),
            ("cx-base_instructions", "base-instructions", "str_nonempty"),
            ("cx-compact_prompt", "compact-prompt", "str_nonempty"),
        ):
            value = self.query_one(f"#{field}", Input).value
            coerced = _coerce_input(value, kind)
            if coerced is not _UNSET:
                out[key] = coerced
        raw_config = self.query_one("#cx-config", Input).value.strip()
        if raw_config:
            try:
                parsed = json.loads(raw_config)
                if isinstance(parsed, dict):
                    out.update(parsed)
            except json.JSONDecodeError:
                pass  # silently ignore malformed JSON; spec'd as advanced power-user input
        return out

    def action_dismiss(self) -> None:
        self.app.pop_screen()


_UNSET = object()


def _coerce_input(raw: str, kind: str) -> Any:
    """Normalise a free-text Input value to the option type, or ``_UNSET`` to skip."""
    if kind == "str_nonempty":
        return raw if raw.strip() else _UNSET
    if kind == "str_tools":
        # `tools` is the one Claude option where empty-string is meaningful
        # (it disables every tool). Distinguish "untouched" (skip) from the
        # explicit `none` sentinel — typing the literal word "none" sends "".
        stripped = raw.strip()
        if not stripped:
            return _UNSET
        if stripped.lower() == "none":
            return ""
        return raw
    if kind == "list":
        items = [s.strip() for s in raw.split(",") if s.strip()]
        return items if items else _UNSET
    return _UNSET

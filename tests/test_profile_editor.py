"""Profile editor edit mode (#12): pre-fill from ``profile.get`` and save a
``profile.update`` that keeps everything the editor doesn't show."""

from __future__ import annotations

import copy

import pytest

from blemees_tui.widgets.modals.agent_editor import (
    REDACTED,
    build_profile_spec,
    merge_profile_spec,
    profile_fields,
)

R = REDACTED

SPEC = {
    "name": "mine",
    "agents": {
        "default": {
            "command": "claude-agent-acp",
            "args": ["--flag", "has space"],
            "model": "sonnet",
            "mode": "plan",
            "agent_home": "/home/a",
            "mcp_servers": [
                {"name": "fs", "command": "fs-mcp", "env": {"TOKEN": R}},
                {"command": "peer", "args": ["p"], "env": {"TOKEN": R}},
            ],
            "env": {"API_KEY": R},
        },
        "reviewer": {"command": "codex-acp", "args": [], "mcp_servers": [], "env": {}},
    },
    "permission_policy": {"mode": "relay", "detached": "stall", "extra": "kept"},
    "notify": {"webhook_url": R, "format": "ntfy"},
    "attention": {"triggers": ["permission"]},
}

MCP_TEXT = (
    '[{"name": "fs", "command": "fs-mcp", "env": {"TOKEN": "<redacted>"}}, '
    '{"command": "peer", "args": ["p"], "env": {"TOKEN": "<redacted>"}}]'
)


# --- profile_fields ------------------------------------------------------


def test_profile_fields_prefills_every_field():
    assert profile_fields(SPEC) == {
        "agent_name": "default",
        "agent_command": "claude-agent-acp",
        "args": "--flag 'has space'",
        "model": "sonnet",
        "agent_home": "/home/a",
        "env": '{"API_KEY": "<redacted>"}',
        "permission_mode": "relay",
        "detached": "stall",
        "mcp_servers": MCP_TEXT,
        "notify_webhook": "<redacted>",
    }


def test_profile_fields_picks_named_agent_and_blanks_empty():
    fields = profile_fields(SPEC, "reviewer")
    assert fields["agent_name"] == "reviewer"
    assert fields["agent_command"] == "codex-acp"
    assert fields["args"] == fields["env"] == fields["mcp_servers"] == fields["model"] == ""


def test_profile_fields_reads_aliases_and_unknown_agent_falls_back():
    spec = {"agents": {"a": {"agent_command": "x", "command": "y", "agent_args": ["1"]}}}
    fields = profile_fields(spec, "nope")
    assert fields["agent_name"] == "a"
    assert fields["agent_command"] == "x"
    assert fields["args"] == "1"


# --- merge_profile_spec ----------------------------------------------------


def test_unchanged_round_trip_is_identity():
    # Sent back untouched, the spec is exactly what profile.get returned —
    # "<redacted>" verbatim, "has space" arg intact.
    assert merge_profile_spec(SPEC, **profile_fields(SPEC)) == SPEC


def test_edit_keeps_what_the_editor_does_not_show():
    before = copy.deepcopy(SPEC)
    fields = profile_fields(SPEC)
    fields.update(model="opus", args="--other", permission_mode="allow")
    spec = merge_profile_spec(SPEC, **fields)
    assert SPEC == before  # base not mutated
    agent = spec["agents"]["default"]
    assert agent["model"] == "opus"
    assert agent["args"] == ["--other"]
    assert agent["mode"] == "plan"
    assert agent["env"] == {"API_KEY": R}
    assert agent["mcp_servers"][0]["env"] == {"TOKEN": R}
    assert spec["agents"]["reviewer"] == SPEC["agents"]["reviewer"]
    assert spec["permission_policy"] == {"mode": "allow", "detached": "stall", "extra": "kept"}
    assert spec["notify"] == {"webhook_url": R, "format": "ntfy"}
    assert spec["attention"] == {"triggers": ["permission"]}
    assert spec["name"] == "mine"


def test_blanked_fields_clear():
    blank = dict.fromkeys(profile_fields(SPEC), "")
    blank["agent_name"] = "default"
    spec = merge_profile_spec(SPEC, **blank)
    agent = spec["agents"]["default"]
    assert agent["args"] == [] and agent["env"] == {} and agent["mcp_servers"] == []
    assert "model" not in agent and "agent_home" not in agent and "command" not in agent
    assert agent["mode"] == "plan"
    assert spec["permission_policy"] == {"extra": "kept"}
    assert spec["notify"] == {"format": "ntfy"}


def test_edits_other_agent_only():
    fields = profile_fields(SPEC, "reviewer")
    fields["model"] = "gpt-5"
    spec = merge_profile_spec(SPEC, agent="reviewer", **fields)
    assert spec["agents"]["reviewer"]["model"] == "gpt-5"
    assert spec["agents"]["default"] == SPEC["agents"]["default"]


def test_command_written_to_command_and_aliases_dropped():
    base = {"agents": {"a": {"agent_command": "old", "command": "x", "agent_args": ["1", "2"]}}}
    spec = merge_profile_spec(base, **profile_fields(base))
    assert spec["agents"]["a"] == {
        "command": "old",
        "args": ["1", "2"],
        "env": {},
        "mcp_servers": [],
    }


def test_replaced_secret_is_sent_and_others_stay_redacted():
    fields = profile_fields(SPEC)
    fields["env"] = '{"API_KEY": "sk-new", "EXTRA": "<redacted>"}'
    spec = merge_profile_spec(SPEC, **fields)
    assert spec["agents"]["default"]["env"] == {"API_KEY": "sk-new", "EXTRA": R}


def test_rename_agent_keeps_position_and_collision_raises():
    fields = profile_fields(SPEC)
    fields["agent_name"] = "developer"
    fields["env"] = '{"API_KEY": "sk-real"}'
    spec = merge_profile_spec(SPEC, **fields)
    assert list(spec["agents"]) == ["developer", "reviewer"]
    assert spec["agents"]["developer"]["env"] == {"API_KEY": "sk-real"}
    fields["agent_name"] = "reviewer"
    with pytest.raises(ValueError, match="already exists"):
        merge_profile_spec(SPEC, **fields)


def test_rename_with_redacted_env_raises():
    base = copy.deepcopy(SPEC)
    base["agents"]["default"]["env"] = {"B_KEY": R, "A_KEY": R, "PLAIN": "x"}
    fields = profile_fields(base)
    fields["agent_name"] = "developer"
    with pytest.raises(ValueError) as err:
        merge_profile_spec(base, **fields)
    assert str(err.value) == (
        "renaming agent 'default' to 'developer' would drop its stored env "
        "secrets (A_KEY, B_KEY): re-enter those values or keep the name"
    )


def test_rename_allowed_without_env_placeholders():
    # reviewer has no env; the default agent keeps "<redacted>" MCP secrets,
    # which the daemon refuses itself if it cannot resolve them.
    fields = profile_fields(SPEC, "reviewer")
    fields["agent_name"] = "critic"
    spec = merge_profile_spec(SPEC, agent="reviewer", **fields)
    assert list(spec["agents"]) == ["default", "critic"]
    assert spec["agents"]["critic"]["command"] == "codex-acp"
    assert spec["agents"]["default"] == SPEC["agents"]["default"]


def test_changed_args_keep_quoted_space():
    # Review r1 #4: args are shell-quoted, so a changed field keeps "has space".
    fields = profile_fields(SPEC)
    fields["args"] += " --new"
    spec = merge_profile_spec(SPEC, **fields)
    assert spec["agents"]["default"]["args"] == ["--flag", "has space", "--new"]
    fields["args"] = "--flag 'has space"
    with pytest.raises(ValueError, match="No closing quotation"):
        merge_profile_spec(SPEC, **fields)
    assert build_profile_spec(args="a 'b c'") == {"agent": {"args": ["a", "b c"]}}
    with pytest.raises(ValueError):
        build_profile_spec(args='"open')


def test_invalid_json_raises():
    fields = profile_fields(SPEC)
    fields["mcp_servers"] = '{"not": "a list"}'
    with pytest.raises(ValueError):
        merge_profile_spec(SPEC, **fields)
    fields = profile_fields(SPEC)
    fields["env"] = '{"X": 1}'
    with pytest.raises(ValueError):
        merge_profile_spec(SPEC, **fields)


# --- app / pilot ------------------------------------------------------------

textual = pytest.importorskip("textual")

from textual.content import Content  # noqa: E402
from textual.widgets import Input, Label  # noqa: E402

from blemees_tui.app import BlemeesTuiApp  # noqa: E402
from blemees_tui.connection import ConnectionError_  # noqa: E402
from blemees_tui.widgets.modals import AgentEditorModal  # noqa: E402

REFUSAL = (
    "invalid_message: cannot resolve <redacted> for "
    "agents.default.mcp_servers[1].env.TOKEN; re-enter the value"
)


class _Fakes:
    def __init__(self, monkeypatch, *, source="dynamic", update_error=None):
        self.created: list[tuple] = []
        self.updated: list[tuple] = []
        self.fetched: list[str] = []
        fakes = self

        async def _noop(self):
            return

        async def get_profile(self, name):
            fakes.fetched.append(name)
            if name != "mine":
                raise ConnectionError_(f"profile_unknown: no profile {name!r}")
            return {
                "type": "profile.info",
                "name": name,
                "source": source,
                "profile": copy.deepcopy(SPEC),
            }

        async def create_profile(self, name, spec):
            fakes.created.append((name, spec))
            if name == "mine":
                raise ConnectionError_(f"profile_exists: profile {name!r} exists")
            return {"type": "profile.created", "name": name}

        async def update_profile(self, name, spec):
            fakes.updated.append((name, spec))
            if update_error:
                raise ConnectionError_(update_error)
            return {"type": "profile.updated", "name": name}

        for attr, fn in (
            ("start", _noop),
            ("stop", _noop),
            ("get_profile", get_profile),
            ("create_profile", create_profile),
            ("update_profile", update_profile),
        ):
            monkeypatch.setattr(f"blemees_tui.connection.Connection.{attr}", fn)


def _plain(label: Label) -> str:
    return str(label.render())


async def _open_editor(app, pilot, name: str) -> AgentEditorModal:
    app.action_edit_agents()
    await pilot.pause()
    modal = app.screen
    assert isinstance(modal, AgentEditorModal)
    name_input = modal.query_one("#p-name", Input)
    name_input.focus()
    name_input.value = name
    await pilot.press("enter")
    await pilot.pause()
    return modal


def _value(modal, sel: str) -> str:
    return modal.query_one(sel, Input).value


@pytest.mark.asyncio
async def test_editor_prefills_existing_profile(isolated_state_dir, monkeypatch):
    fakes = _Fakes(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = await _open_editor(app, pilot, "mine")
        assert fakes.fetched == ["mine"]
        assert modal.editing
        assert _value(modal, "#p-agent_name") == "default"
        assert _value(modal, "#p-agent_command") == "claude-agent-acp"
        assert _value(modal, "#p-args") == "--flag 'has space'"
        assert _value(modal, "#p-model") == "sonnet"
        assert _value(modal, "#p-agent_home") == "/home/a"
        assert _value(modal, "#p-env") == '{"API_KEY": "<redacted>"}'
        assert _value(modal, "#p-permission_mode") == "relay"
        assert _value(modal, "#p-detached") == "stall"
        assert _value(modal, "#p-mcp") == MCP_TEXT
        assert _value(modal, "#p-notify_webhook") == "<redacted>"
        status = _plain(modal.query_one("#p-status", Label))
        assert "agents: default, reviewer" in status and "editing default" in status
        assert "keeps the stored secret" in _plain(modal.query_one("#p-hint", Label))


@pytest.mark.asyncio
async def test_editor_prefills_default_profile_on_mount(isolated_state_dir, monkeypatch):
    _Fakes(monkeypatch, source="config")
    app = BlemeesTuiApp(profile_override="mine")
    async with app.run_test() as pilot:
        app.action_edit_agents()
        await pilot.pause()
        modal = app.screen
        assert _value(modal, "#p-model") == "sonnet"
        assert "config-managed" in _plain(modal.query_one("#p-status", Label))


@pytest.mark.asyncio
async def test_edit_save_updates_with_merged_spec(isolated_state_dir, monkeypatch):
    fakes = _Fakes(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = await _open_editor(app, pilot, "mine")
        modal.query_one("#p-model", Input).value = "opus"
        modal.query_one("#save").press()
        await pilot.pause()
        assert fakes.created == []
        assert len(fakes.updated) == 1
        name, spec = fakes.updated[0]
        assert name == "mine"
        expected = copy.deepcopy(SPEC)
        expected["agents"]["default"]["model"] = "opus"
        assert spec == expected
        assert not isinstance(app.screen, AgentEditorModal)
        assert "profile_saved" in [e.category for e in app.state.event_log]


@pytest.mark.asyncio
async def test_edit_save_refusal_stays_open_with_full_message(isolated_state_dir, monkeypatch):
    fakes = _Fakes(monkeypatch, update_error=REFUSAL)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = await _open_editor(app, pilot, "mine")
        modal.query_one("#p-model", Input).value = "opus"
        modal.query_one("#save").press()
        await pilot.pause()
        assert len(fakes.updated) == 1
        # Still open, edits kept, daemon message shown verbatim (brackets intact).
        assert app.screen is modal
        assert _value(modal, "#p-model") == "opus"
        assert REFUSAL in _plain(modal.query_one("#p-error", Label))
        assert not modal.query_one("#save").disabled
        log = [(e.category, e.message) for e in app.state.event_log]
        assert ("profile_save_failed", REFUSAL) in log
        toasts = [Content.from_markup(n.message).plain for n in app._notifications]
        assert any(REFUSAL in t for t in toasts)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        REFUSAL,
        # Word-like brackets are eaten by Textual's parser unless escaped, and
        # a stray closing tag raises MarkupError.
        "invalid_message: cannot resolve <redacted> for headers[Authorization]",
        "agent_unavailable: 'x[/y]' not on PATH",
    ],
)
async def test_create_failure_toast_keeps_brackets(isolated_state_dir, monkeypatch, error):
    """The toast escapes the daemon's message (create path, unchanged API)."""

    async def _noop(self):
        return

    async def fake_create(self, name, spec):
        raise ConnectionError_(error)

    monkeypatch.setattr("blemees_tui.connection.Connection.start", _noop)
    monkeypatch.setattr("blemees_tui.connection.Connection.stop", _noop)
    monkeypatch.setattr("blemees_tui.connection.Connection.create_profile", fake_create)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        await app.on_agent_editor_modal_save_profile(
            AgentEditorModal.SaveProfile("mine", {"agent": {}})
        )
        await pilot.pause()
        toasts = [Content.from_markup(n.message).plain for n in app._notifications]
        assert any(error in t for t in toasts)


@pytest.mark.asyncio
async def test_unknown_profile_stays_in_create_mode(isolated_state_dir, monkeypatch):
    fakes = _Fakes(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = await _open_editor(app, pilot, "fresh")
        assert fakes.fetched == ["fresh"]
        assert not modal.editing
        assert all(
            _value(modal, sel) == ""
            for sel in ("#p-agent_name", "#p-agent_command", "#p-args", "#p-env", "#p-mcp")
        )
        modal.query_one("#p-agent_command", Input).value = "claude-agent-acp"
        modal.query_one("#save").press()
        await pilot.pause()
        assert fakes.created == [("fresh", {"agent": {"agent_command": "claude-agent-acp"}})]
        assert fakes.updated == []
        assert not isinstance(app.screen, AgentEditorModal)


@pytest.mark.asyncio
async def test_copy_to_new_name_refuses_redacted_then_creates(isolated_state_dir, monkeypatch):
    # Review r1 #3: a copy gets no stored secrets, so "<redacted>" must go.
    fakes = _Fakes(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = await _open_editor(app, pilot, "mine")
        assert modal.editing
        modal.query_one("#p-name", Input).value = "copy"
        await pilot.pause()
        assert not modal.editing
        status = _plain(modal.query_one("#p-status", Label))
        assert "new profile 'copy' — secrets are not copied; re-enter them" in status
        assert _plain(modal.query_one("#p-hint", Label)) == ""
        assert _plain(modal.query_one("#p-title", Label)) == "Add agent to profile"
        modal.query_one("#save").press()
        await pilot.pause()
        assert fakes.created == [] and fakes.updated == []
        assert app.screen is modal
        error = _plain(modal.query_one("#p-error", Label))
        assert "env, mcp_servers, notify_webhook" in error and "re-enter" in error
        modal.query_one("#p-env", Input).value = '{"API_KEY": "sk-copy"}'
        modal.query_one("#p-mcp", Input).value = ""
        modal.query_one("#p-notify_webhook", Input).value = "https://hook"
        modal.query_one("#save").press()
        await pilot.pause()
        assert fakes.fetched == ["mine", "copy", "copy"]  # each Save checks again
        assert fakes.updated == []
        [(name, spec)] = fakes.created
        assert name == "copy"
        agent = spec["agents"]["default"]
        assert agent["env"] == {"API_KEY": "sk-copy"}
        assert agent["args"] == ["--flag", "has space"]
        assert spec["notify"] == {"webhook_url": "https://hook"}


@pytest.mark.asyncio
async def test_old_daemon_without_profile_get_creates(isolated_state_dir, monkeypatch):
    fakes = _Fakes(monkeypatch)

    async def old(self, name):
        raise ConnectionError_("unknown_message: unknown message type 'profile.get'")

    monkeypatch.setattr("blemees_tui.connection.Connection.get_profile", old)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        app.action_edit_agents()
        await pilot.pause()
        modal = app.screen
        modal.query_one("#p-name", Input).value = "fresh"
        modal.query_one("#p-model", Input).value = "opus"
        modal.query_one("#save").press()
        await pilot.pause()
        assert fakes.created == [("fresh", {"agent": {"model": "opus"}})]


@pytest.mark.asyncio
async def test_save_fetch_error_does_not_save(isolated_state_dir, monkeypatch):
    fakes = _Fakes(monkeypatch)

    async def broken(self, name):
        raise ConnectionError_("internal: boom [x]")

    monkeypatch.setattr("blemees_tui.connection.Connection.get_profile", broken)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        app.action_edit_agents()
        await pilot.pause()
        modal = app.screen
        modal.query_one("#p-name", Input).value = "mine"
        modal.query_one("#save").press()
        await pilot.pause()
        assert fakes.created == [] and fakes.updated == []
        assert app.screen is modal
        assert "not saved: profile.get failed: internal: boom [x]" in _plain(
            modal.query_one("#p-error", Label)
        )


@pytest.mark.asyncio
async def test_edit_title_and_rename_hint(isolated_state_dir, monkeypatch):
    _Fakes(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        app.action_edit_agents()
        await pilot.pause()
        modal = app.screen
        assert _plain(modal.query_one("#p-title", Label)) == "Add agent to profile"
        modal = await _open_editor(app, pilot, "mine")
        assert _plain(modal.query_one("#p-title", Label)) == "Edit profile"
        assert "a new agent name renames 'default'" in _plain(modal.query_one("#p-status", Label))


@pytest.mark.asyncio
async def test_agent_switch_refused_with_unsaved_changes(isolated_state_dir, monkeypatch):
    # Review r1 #5: Enter on another agent name would drop the edits.
    _Fakes(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = await _open_editor(app, pilot, "mine")
        modal.query_one("#p-model", Input).value = "opus"
        agent_input = modal.query_one("#p-agent_name", Input)
        agent_input.focus()
        agent_input.value = "reviewer"
        await pilot.press("enter")
        await pilot.pause()
        assert _value(modal, "#p-model") == "opus"
        assert _value(modal, "#p-agent_command") == "claude-agent-acp"
        assert "unsaved changes to 'default' — Save first or revert them" in _plain(
            modal.query_one("#p-error", Label)
        )
        # Reverted: the switch goes through.
        modal.query_one("#p-model", Input).value = "sonnet"
        agent_input.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert _value(modal, "#p-agent_command") == "codex-acp"
        assert "editing reviewer" in _plain(modal.query_one("#p-status", Label))


@pytest.mark.asyncio
async def test_unbalanced_quote_in_args_is_shown(isolated_state_dir, monkeypatch):
    fakes = _Fakes(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = await _open_editor(app, pilot, "mine")
        modal.query_one("#p-args", Input).value = "--flag 'has space"
        modal.query_one("#save").press()
        await pilot.pause()
        assert fakes.updated == []
        assert "No closing quotation" in _plain(modal.query_one("#p-error", Label))


@pytest.mark.asyncio
async def test_fetch_error_is_shown_not_fatal(isolated_state_dir, monkeypatch):
    _Fakes(monkeypatch)

    async def broken(self, name):
        raise ConnectionError_("internal: boom [x]")

    monkeypatch.setattr("blemees_tui.connection.Connection.get_profile", broken)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = await _open_editor(app, pilot, "mine")
        assert not modal.editing
        assert "internal: boom [x]" in _plain(modal.query_one("#p-status", Label))


@pytest.mark.asyncio
async def test_save_typed_existing_name_loads_instead_of_replacing(isolated_state_dir, monkeypatch):
    # Review r1 #1: the name typed without Enter, then Save. The old flow sent
    # create → profile_exists → update with the small spec (a full replace).
    fakes = _Fakes(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        app.action_edit_agents()
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, AgentEditorModal)
        modal.query_one("#p-name", Input).value = "mine"
        modal.query_one("#p-model", Input).value = "opus"
        await pilot.pause()
        modal.query_one("#save").press()
        await pilot.pause()
        assert ("mine", {"agent": {"model": "opus"}}) not in fakes.updated
        assert fakes.updated == [] and fakes.created == []
        assert app.screen is modal and modal.editing
        assert _value(modal, "#p-agent_command") == "claude-agent-acp"
        assert _value(modal, "#p-model") == "sonnet"
        assert "loaded existing profile 'mine'" in _plain(modal.query_one("#p-error", Label))


@pytest.mark.asyncio
async def test_save_typed_existing_name_names_replaced_fields(isolated_state_dir, monkeypatch):
    # Review r2 nit: the load replaces typed values; the error must name them.
    _Fakes(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        app.action_edit_agents()
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, AgentEditorModal)
        modal.query_one("#p-name", Input).value = "mine"
        modal.query_one("#p-model", Input).value = "opus"
        modal.query_one("#p-agent_command", Input).value = "claude-agent-acp"  # same as stored
        await pilot.pause()
        modal.query_one("#save").press()
        await pilot.pause()
        error = _plain(modal.query_one("#p-error", Label))
        assert "the stored values replaced what you typed in: model —" in error
        assert "agent_command" not in error


@pytest.mark.asyncio
async def test_name_changed_and_back_restores_edit_mode(isolated_state_dir, monkeypatch):
    # Review r1 #2: mine → min → mine must still save the merged spec.
    fakes = _Fakes(monkeypatch)
    app = BlemeesTuiApp()
    async with app.run_test() as pilot:
        modal = await _open_editor(app, pilot, "mine")
        modal.query_one("#p-model", Input).value = "opus"
        name_input = modal.query_one("#p-name", Input)
        name_input.value = "min"
        await pilot.pause()
        name_input.value = "mine"
        await pilot.pause()
        assert modal.editing
        assert "editing mine" in _plain(modal.query_one("#p-status", Label))
        assert "keeps the stored secret" in _plain(modal.query_one("#p-hint", Label))
        modal.query_one("#save").press()
        await pilot.pause()
        assert fakes.created == []  # update=True: no create attempt
        expected = copy.deepcopy(SPEC)
        expected["agents"]["default"]["model"] = "opus"
        assert fakes.updated == [("mine", expected)]

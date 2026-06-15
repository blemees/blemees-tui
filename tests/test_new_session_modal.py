"""Profile-spec builder for the new-session / profile-management modal (#5)."""

from __future__ import annotations

import pytest

from blemees_tui.widgets.modals.agent_editor import build_profile_spec


def test_blank_fields_omitted():
    # An empty editor yields an empty agent and nothing else — the daemon
    # keeps its defaults.
    assert build_profile_spec() == {"agent": {}}


def test_agent_fields_go_under_agent():
    spec = build_profile_spec(
        agent_command="claude-agent-acp", args="acp --flag", model="sonnet", agent_home="/proj"
    )
    assert spec["agent"] == {
        "agent_command": "claude-agent-acp",
        "args": ["acp", "--flag"],
        "model": "sonnet",
        "agent_home": "/proj",
    }
    assert "permission_policy" not in spec
    assert "notify" not in spec


def test_permission_policy_and_notify_at_top_level():
    spec = build_profile_spec(permission_mode="relay", detached="stall", notify_webhook="https://h")
    assert spec["permission_policy"] == {"mode": "relay", "detached": "stall"}
    assert spec["notify"] == {"webhook_url": "https://h"}


def test_partial_permission_policy():
    spec = build_profile_spec(permission_mode="allow")
    assert spec["permission_policy"] == {"mode": "allow"}


def test_mcp_servers_parsed_as_json_under_agent():
    spec = build_profile_spec(mcp_servers='[{"name": "peer", "command": "x"}]')
    assert spec["agent"]["mcp_servers"] == [{"name": "peer", "command": "x"}]


def test_mcp_servers_invalid_json_raises():
    with pytest.raises(ValueError):
        build_profile_spec(mcp_servers="{not json")


def test_mcp_servers_non_array_raises():
    with pytest.raises(ValueError):
        build_profile_spec(mcp_servers='{"name": "x"}')


def test_whitespace_only_fields_skipped():
    assert build_profile_spec(model="   ", permission_mode="  ") == {"agent": {}}


# --- Modal UI: agent selection (profile is fixed at launch) ---------------

textual = pytest.importorskip("textual")

from textual.app import App  # noqa: E402
from textual.widgets import RadioSet  # noqa: E402

from blemees_tui.widgets.modals.new_session import NewSessionModal  # noqa: E402


class _Host(App):
    def __init__(self, modal):
        super().__init__()
        self._modal = modal
        self.opened = []

    def on_mount(self):
        self.push_screen(self._modal)

    def on_new_session_modal_open_session(self, msg: NewSessionModal.OpenSession):
        self.opened.append(msg)


async def _profiles():
    return [
        {"name": "blemees", "source": "config", "agents": [
            {"name": "developer", "model": "opus"},
            {"name": "architect", "model": None},
        ]},
        {"name": "other", "agents": [{"name": "x"}]},
    ]


@pytest.mark.asyncio
async def test_modal_lists_agents_of_fixed_profile_no_profile_picker():
    modal = NewSessionModal(_profiles, default_cwd="/work", profile="blemees")
    app = _Host(modal)
    async with app.run_test() as pilot:
        await pilot.pause()
        # No profile picker exists; the picker is the agent radio.
        assert not modal.query("#profiles")
        ids = [rb.id for rb in modal.query_one("#agents", RadioSet).query("RadioButton")]
        assert ids == ["agent-developer", "agent-architect"]
        # The fixed profile is shown, not selectable.
        assert "blemees" in str(modal.query_one("#profile-name").render())


@pytest.mark.asyncio
async def test_modal_open_posts_profile_and_selected_agent():
    modal = NewSessionModal(_profiles, default_cwd="/work", profile="blemees")
    app = _Host(modal)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal._do_open()  # developer is selected by default (first)
        await pilot.pause()
        assert len(app.opened) == 1
        msg = app.opened[0]
        assert (msg.profile, msg.agent, msg.cwd) == ("blemees", "developer", "/work")


@pytest.mark.asyncio
async def test_modal_adopts_first_profile_when_launch_profile_blank():
    modal = NewSessionModal(_profiles, default_cwd="/work", profile="")
    app = _Host(modal)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert modal._profile == "blemees"  # first reported profile


# --- build_profile_spec: agent_name + env (agent_editor) ------------------

def test_agent_name_nests_under_agents_map():
    spec = build_profile_spec(agent_name="developer", agent_command="claude-agent-acp")
    assert spec == {"agents": {"developer": {"agent_command": "claude-agent-acp"}}}


def test_env_parsed_as_json_object():
    spec = build_profile_spec(env='{"ANTHROPIC_LOG": "debug"}')
    assert spec["agent"]["env"] == {"ANTHROPIC_LOG": "debug"}


def test_env_non_object_raises():
    with pytest.raises(ValueError):
        build_profile_spec(env='["not", "an", "object"]')


def test_env_non_string_values_raise():
    with pytest.raises(ValueError):
        build_profile_spec(env='{"X": 1}')

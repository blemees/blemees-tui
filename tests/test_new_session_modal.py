"""Profile-spec builder for the new-session / profile-management modal (#5)."""

from __future__ import annotations

import pytest

from blemees_tui.widgets.modals.new_session import build_profile_spec


def test_blank_fields_omitted():
    # An empty editor yields an empty agent and nothing else — the daemon
    # keeps its defaults.
    assert build_profile_spec() == {"agent": {}}


def test_agent_fields_go_under_agent():
    spec = build_profile_spec(
        agent_command="claude-agent-acp", args="acp --flag", model="sonnet", cwd="/proj"
    )
    assert spec["agent"] == {
        "agent_command": "claude-agent-acp",
        "args": ["acp", "--flag"],
        "model": "sonnet",
        "cwd": "/proj",
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

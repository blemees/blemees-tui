"""Filesystem skill discovery tests (M4.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blemees_tui.discover import (
    BUILTIN_CC_COMMANDS,
    filter_suggestions,
    list_skills,
    parse_help_output,
    slash_suggestions,
)


@pytest.fixture
def fake_claude_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path))
    return tmp_path


def _write_skill(path: Path, name: str, description: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n# {name}\nbody.\n',
        encoding="utf-8",
    )


def test_list_skills_handles_both_layouts(fake_claude_home: Path):
    # Layout A: skills/<name>.md
    _write_skill(fake_claude_home / "skills" / "review.md", "review", "Review code")
    # Layout B: skills/<name>/SKILL.md
    _write_skill(
        fake_claude_home / "skills" / "init" / "SKILL.md",
        "init",
        "Initialise CLAUDE.md",
    )
    out = list_skills()
    labels = {s.label for s in out}
    assert "/review" in labels
    assert "/init" in labels
    by_label = {s.label: s for s in out}
    assert by_label["/review"].description == "Review code"
    assert by_label["/init"].source == "skill"


def test_list_skills_picks_up_plugin_subdirs(fake_claude_home: Path):
    _write_skill(
        fake_claude_home / "plugins" / "atlas" / "skills" / "deploy.md",
        "deploy",
        "Deploy a service",
    )
    out = list_skills()
    deploy = next(s for s in out if s.label == "/deploy")
    assert deploy.source == "plugin:atlas"
    assert deploy.description == "Deploy a service"


def test_list_skills_when_no_claude_home(fake_claude_home: Path):
    # Empty $CLAUDE_HOME directory → no skills.
    assert list_skills() == []


def test_slash_suggestions_unions_builtins_and_skills(fake_claude_home: Path):
    _write_skill(fake_claude_home / "skills" / "summarise.md", "summarise", "TLDR;")
    out = slash_suggestions()
    labels = {s.label for s in out}
    for builtin in BUILTIN_CC_COMMANDS:
        assert builtin.label in labels
    assert "/summarise" in labels


def test_filter_suggestions_prefix_priority():
    items = list(BUILTIN_CC_COMMANDS)
    out = filter_suggestions(items, "/co")
    labels = {s.label for s in out}
    # Both /context, /compact, /cost start with /co.
    assert {"/context", "/compact", "/cost"}.issubset(labels)
    # /agents (substring "co" not present) must NOT appear.
    assert "/agents" not in labels


def test_filter_suggestions_empty_typed_returns_all():
    items = list(BUILTIN_CC_COMMANDS)
    assert filter_suggestions(items, "") == items


def test_filter_suggestions_substring_fallback():
    items = list(BUILTIN_CC_COMMANDS)
    out = filter_suggestions(items, "/cost")
    assert any(s.label == "/cost" for s in out)


def test_parse_help_output_claude_real_format():
    """Realistic ``/help`` reply format from Claude Code."""
    text = """\
Session & context

  • /help — show help
  • /clear — clear conversation history
  • /compact — summarize and compress conversation
  • /context — show token usage breakdown
  • /exit (or /quit) — exit the CLI

Configuration

  • /model — switch model
  • /login, /logout — auth
"""
    out = parse_help_output(text)
    labels = [s.label for s in out]
    assert "/help" in labels
    assert "/clear" in labels
    assert "/compact" in labels
    assert "/context" in labels
    assert "/exit" in labels
    assert "/model" in labels
    by_label = {s.label: s for s in out}
    assert by_label["/clear"].description == "clear conversation history"
    assert by_label["/help"].source == "probed"


def test_parse_help_output_handles_dash_separator():
    text = """\
* /foo - first
- /bar - second
"""
    out = parse_help_output(text)
    assert {s.label for s in out} == {"/foo", "/bar"}


def test_parse_help_output_returns_empty_on_garbage():
    assert parse_help_output("hello world") == []
    assert parse_help_output("") == []

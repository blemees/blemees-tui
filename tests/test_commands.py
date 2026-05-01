"""TUI command parser tests (vim-style ``:`` prefix, M4.2)."""

from __future__ import annotations

from blemees_tui.commands import completions, is_uuid, parse


def test_plain_text_is_not_a_command():
    assert parse("hello world") is None
    assert parse("") is None
    assert parse(":") is None


def test_slash_prefix_does_not_match():
    """``/`` is reserved for backend slash commands and must NOT be
    treated as a TUI command anymore."""
    assert parse("/new") is None
    assert parse("/skill-name arg") is None


def test_known_command_no_arg():
    cmd = parse(":new")
    assert cmd is not None
    assert cmd.name == "new"
    assert cmd.arg == ""
    assert not cmd.is_unknown


def test_known_command_with_arg():
    cmd = parse(":rename refactor utils.py")
    assert cmd is not None
    assert cmd.name == "rename"
    assert cmd.arg == "refactor utils.py"


def test_unknown_command_flagged():
    cmd = parse(":foo bar")
    assert cmd is not None
    assert cmd.is_unknown
    assert cmd.name == "foo"


def test_quit_aliases():
    assert parse(":q").name == "q"
    assert parse(":quit").name == "quit"
    assert not parse(":q").is_unknown
    assert not parse(":quit").is_unknown


def test_uuid_validation():
    assert is_uuid("5a01abcd-1234-5678-9abc-def012345678")
    assert not is_uuid("not-a-uuid")
    assert not is_uuid("")


def test_completions():
    assert ":close" in completions(":c")
    assert ":cwd" in completions(":c")
    assert ":new" in completions(":n")
    assert completions("hello") == []
    assert completions("/c") == []

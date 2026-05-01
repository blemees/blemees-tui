"""NewSession modal option-coercion tests (regression for empty
``permission_mode`` crashing the Claude backend)."""

from __future__ import annotations

from blemees_tui.widgets.modals.new_session import _UNSET, _coerce_input


def test_blank_permission_mode_is_skipped():
    """Regression: blank `permission_mode` was being sent as `""`, which
    fails Claude's arg validation. Must skip."""
    assert _coerce_input("", "str_nonempty") is _UNSET
    assert _coerce_input("   ", "str_nonempty") is _UNSET


def test_str_nonempty_keeps_real_values():
    assert _coerce_input("acceptEdits", "str_nonempty") == "acceptEdits"


def test_str_tools_blank_skips():
    assert _coerce_input("", "str_tools") is _UNSET
    assert _coerce_input("   ", "str_tools") is _UNSET


def test_str_tools_none_sentinel_disables_all():
    assert _coerce_input("none", "str_tools") == ""
    assert _coerce_input("NONE", "str_tools") == ""


def test_str_tools_real_value_passes_through():
    assert _coerce_input("Read,Edit", "str_tools") == "Read,Edit"


def test_list_blank_skips_empty_list_and_real_values():
    assert _coerce_input("", "list") is _UNSET
    assert _coerce_input(",,", "list") is _UNSET
    assert _coerce_input("a, b ,c", "list") == ["a", "b", "c"]

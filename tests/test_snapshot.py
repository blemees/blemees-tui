"""Snapshot persistence — full SessionState round-trip to disk."""

from __future__ import annotations

from pathlib import Path

import pytest

from blemees_tui import persistence
from blemees_tui.snapshot import (
    delete_snapshot,
    list_snapshot_ids,
    load_snapshot,
    save_snapshot,
    session_from_dict,
    session_to_dict,
)
from blemees_tui.state import (
    SessionMode,
    SessionState,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Turn,
    Usage,
)


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch):
    """Redirect persistence's state-dir resolver to a tmp path."""
    monkeypatch.setattr(persistence, "state_dir", lambda: tmp_path)
    yield tmp_path


def _populated_session() -> SessionState:
    sess = SessionState(
        session_id="abc123",
        backend="claude",
        agent="developer",
        model="sonnet",
        cwd="/tmp",
        title="Demo",
        options={"permission_mode": "default"},
        mode=SessionMode.OWNED,
        last_seq=42,
        last_seen_seq=42,
        last_active_at_ms=1_700_000_000_000,
        started_at_ms=1_699_999_999_000,
        context_tokens=12_345,
        context_window=200_000,
        cumulative_usage=Usage(input_tokens=100, output_tokens=200),
        draft="half-typed message",
    )
    turn = Turn(
        user_text="please read /x",
        blocks=[
            TextBlock(text="Reading the file.", finalized=True),
            ToolUseBlock(
                tool_use_id="tu1",
                name="Read",
                input={"path": "/x"},
                result_text="contents",
                is_error=False,
            ),
            TextBlock(text="Done.", finalized=True),
            ThinkingBlock(text="hmm", finalized=True),
        ],
        duration_ms=1500,
        usage=Usage(input_tokens=50, output_tokens=100),
        result_subtype="success",
        locked=True,
    )
    sess.turns.append(turn)
    return sess


def test_session_to_dict_round_trip_preserves_blocks_and_usage():
    sess = _populated_session()
    restored = session_from_dict(session_to_dict(sess))
    assert restored is not None
    assert restored.session_id == "abc123"
    assert restored.backend == "claude"
    assert restored.agent == "developer"
    assert restored.model == "sonnet"
    assert restored.draft == "half-typed message"
    assert restored.mode == SessionMode.OWNED
    assert restored.last_seen_seq == 42
    assert restored.cumulative_usage.input_tokens == 100
    assert len(restored.turns) == 1

    turn = restored.turns[0]
    assert turn.user_text == "please read /x"
    assert turn.locked is True
    assert turn.duration_ms == 1500
    shapes = [type(b).__name__ for b in turn.blocks]
    assert shapes == ["TextBlock", "ToolUseBlock", "TextBlock", "ThinkingBlock"]

    tool = turn.blocks[1]
    assert isinstance(tool, ToolUseBlock)
    assert tool.tool_use_id == "tu1"
    assert tool.input == {"path": "/x"}
    assert tool.result_text == "contents"


def test_save_load_delete_round_trip(state_dir):
    sess = _populated_session()
    save_snapshot(sess)
    assert "abc123" in list_snapshot_ids()

    restored = load_snapshot("abc123")
    assert restored is not None
    assert restored.title == "Demo"
    assert len(restored.turns) == 1

    delete_snapshot("abc123")
    assert load_snapshot("abc123") is None
    assert "abc123" not in list_snapshot_ids()


def test_load_snapshot_missing_returns_none(state_dir):
    assert load_snapshot("nope") is None


def test_load_snapshot_schema_mismatch_returns_none(state_dir):
    # Write a version we don't understand.
    payload = session_to_dict(_populated_session())
    payload["version"] = 99_999
    persistence.atomic_write_json(persistence.snapshot_path("abc123"), payload)
    assert load_snapshot("abc123") is None


def test_marked_flag_round_trips(state_dir):
    """Broadcast marks must survive snapshot save → load."""
    sess = _populated_session()
    sess.marked = True
    save_snapshot(sess)
    restored = load_snapshot(sess.session_id)
    assert restored is not None
    assert restored.marked is True


def test_transient_fields_are_not_persisted():
    """Replay markers, pending_errors etc. must not leak to disk — they're
    rebuilt from incoming frames on next attach."""
    sess = _populated_session()
    sess.replay_target_seq = 999
    sess.replay_start_seq = 100
    sess.pending_errors = [{"code": "x"}]
    sess.pending_sends = ["queued"]
    sess.turn_active = True

    payload = session_to_dict(sess)
    assert "replay_target_seq" not in payload
    assert "replay_start_seq" not in payload
    assert "pending_errors" not in payload
    assert "pending_sends" not in payload
    assert "turn_active" not in payload

    restored = session_from_dict(payload)
    assert restored is not None
    assert restored.replay_target_seq == 0
    assert restored.pending_errors == []
    assert restored.pending_sends == []
    assert restored.turn_active is False

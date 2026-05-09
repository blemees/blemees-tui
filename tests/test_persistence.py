"""Persistence tests (spec §13)."""

from __future__ import annotations

from pathlib import Path

from blemees_tui.persistence import (
    StoredSession,
    atomic_write_json,
    load_sessions,
    save_sessions,
    slugify,
    transcript_filename,
)


def test_atomic_write_json_creates_file(tmp_path: Path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"a": 1})
    assert p.exists()
    assert "a" in p.read_text(encoding="utf-8")
    assert not p.with_suffix(".json.tmp").exists()


def test_sessions_round_trip(tmp_path: Path):
    p = tmp_path / "sessions.json"
    rows = [
        StoredSession(
            session_id="s1",
            backend="claude",
            model="sonnet",
            cwd="/proj",
            title="t",
            options={"x": 1},
            last_seen_seq=42,
            last_active_at_ms=1700000000000,
            mode="owned",
            marked=True,
        )
    ]
    save_sessions(rows, p)
    loaded = load_sessions(p)
    assert len(loaded) == 1
    assert loaded[0].session_id == "s1"
    assert loaded[0].last_seen_seq == 42
    assert loaded[0].options == {"x": 1}
    assert loaded[0].marked is True


def test_sessions_load_defaults_marked_to_false_for_old_files(tmp_path: Path):
    """sessions.json files written before the broadcast feature shipped
    don't carry a ``marked`` field. Loader must default it cleanly."""
    p = tmp_path / "sessions.json"
    p.write_text(
        '{"version": 1, "sessions": [{"session_id": "s1", "backend": "claude", '
        '"model": "", "cwd": "", "title": "", "options": {}, "last_seen_seq": 0, '
        '"last_active_at_ms": 0, "mode": "owned"}]}',
        encoding="utf-8",
    )
    loaded = load_sessions(p)
    assert len(loaded) == 1
    assert loaded[0].marked is False


def test_slugify():
    assert slugify("Refactor utils.py") == "refactor-utils-py"
    assert slugify("   ") == "session"


def test_transcript_filename():
    name = transcript_filename("Refactor utils.py", "5a01abcd-1234-5678-9abc-def012345678")
    assert name.endswith(".md")
    assert "5a01abcd" in name
    assert "refactor-utils-py" in name


def test_load_sessions_missing_file(tmp_path: Path):
    assert load_sessions(tmp_path / "nope.json") == []


def test_load_sessions_corrupt_returns_empty(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_sessions(p) == []

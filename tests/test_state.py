"""state.py sanity checks."""

from __future__ import annotations

from blemees_tui.state import EventLog, EventLogSource, Usage


def test_usage_merge():
    a = Usage(input_tokens=1, output_tokens=2)
    b = Usage(input_tokens=10, output_tokens=20, reasoning_output_tokens=5)
    out = a.merge(b)
    assert out.input_tokens == 11
    assert out.output_tokens == 22
    assert out.reasoning_output_tokens == 5


def test_event_log_bounded():
    log = EventLog(capacity=3)
    for i in range(5):
        log.append(EventLogSource.NOTICE, "tag", f"m{i}")
    snapshot = log.snapshot()
    assert len(snapshot) == 3
    assert [e.message for e in snapshot] == ["m2", "m3", "m4"]

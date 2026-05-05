"""EventLogOverlay filtering (M3.4)."""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")
from textual.app import App  # noqa: E402

from blemees_tui.state import EventLog, EventLogSource  # noqa: E402
from blemees_tui.widgets.event_log import EventLogOverlay  # noqa: E402


class _Host(App):
    pass


@pytest.mark.asyncio
async def test_event_log_filter_by_source_and_text(isolated_state_dir):
    log = EventLog(capacity=100)
    log.append(EventLogSource.NOTICE, "rate_limits", "resets in 4m")
    log.append(EventLogSource.DAEMON_ERROR, "auth_failed", "login expired")
    log.append(EventLogSource.CONNECTION, "hello", "connected to blemees-agentd/0.9.2")
    log.append(EventLogSource.DAEMON_ERROR, "spawn_failed", "no claude on PATH")

    app = _Host()
    async with app.run_test() as pilot:
        screen = EventLogOverlay(log)
        await app.push_screen(screen)
        await pilot.pause()

        # All by default — every entry visible.
        assert len(screen._filtered()) == 4

        # Filter by source.
        screen._source = EventLogSource.DAEMON_ERROR
        rows = screen._filtered()
        assert len(rows) == 2
        assert all(r.source == EventLogSource.DAEMON_ERROR for r in rows)

        # Layer text needle on top.
        screen._needle = "spawn"
        rows = screen._filtered()
        assert len(rows) == 1
        assert rows[0].category == "spawn_failed"

        # Save action writes a file.
        screen.action_save()
        # The saved file lives in $XDG_STATE_HOME/blemees/tui/.
        out_dir = isolated_state_dir / "blemees" / "tui"
        files = list(out_dir.glob("event-log-*.txt"))
        assert files
        assert "spawn_failed" in files[0].read_text(encoding="utf-8")

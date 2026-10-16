"""End-to-end smoke: TUI ↔ real blemees-agentd ↔ real ACP agent (#13).

Every other TUI test mocks the socket. These drive the real app through
Textual's ``Pilot`` against a ``blemees-agentd`` subprocess spawning a real
``claude-agent-acp``, so wiring bugs (frame round-trips, focus, async
refresh) show up. They cover: connect, open a session under a profile, send
a prompt, see streamed text + turn end, answer an inline permission card,
and reattach as a viewer from a second TUI.

Gated like the agent e2e matrix (blemees-agent #26): opt-in, and skipped if
the agent never answers (not installed / not logged in)::

    BLEMEES_E2E=1 pytest tests/test_e2e_smoke.py -v

Overrides:

* ``BLEMEES_E2E_AGENTD`` — daemon binary (default: ``blemees-agentd`` on PATH).
* ``BLEMEES_E2E_AGENT`` — ACP agent command line, shell-split (default:
  ``claude-agent-acp``). Point it at blemees-agent's
  ``tests/blemees_agent/fake_acp.py`` to check the harness without tokens.

These never run in CI. The manual version is ``docs/SMOKE.md``.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

textual = pytest.importorskip("textual")

from blemees_tui.app import BlemeesTuiApp  # noqa: E402
from blemees_tui.state import SessionMode, SessionState, TextBlock  # noqa: E402
from blemees_tui.widgets import ChatPaneWidget, ComposerWidget  # noqa: E402
from blemees_tui.widgets.modals import AttachModal  # noqa: E402
from blemees_tui.widgets.modals.new_session import NewSessionModal  # noqa: E402

PROFILE = "smoke"

_E2E_ENABLED = os.environ.get("BLEMEES_E2E") == "1"
_AGENTD = os.environ.get("BLEMEES_E2E_AGENTD") or shutil.which("blemees-agentd")
_AGENT_CMD = shlex.split(os.environ.get("BLEMEES_E2E_AGENT") or "claude-agent-acp")


def _skip_reason() -> str | None:
    if not _E2E_ENABLED:
        return "set BLEMEES_E2E=1 to run the real-daemon smoke"
    if not _AGENTD:
        return "blemees-agentd not on PATH (or set BLEMEES_E2E_AGENTD)"
    if not _AGENT_CMD or shutil.which(_AGENT_CMD[0]) is None:
        return (
            f"{_AGENT_CMD[0] if _AGENT_CMD else '(empty)'} not on PATH (or set BLEMEES_E2E_AGENT)"
        )
    return None


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or ""),
]


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@pytest.fixture(scope="module")
def daemon_socket() -> Iterator[str]:
    """Run one blemees-agentd for the module with a single ``smoke`` profile.

    Unix socket paths are length-limited (~104 bytes on macOS), so the run
    dir lives under /tmp, not pytest's long tmp_path.
    """
    run_dir = Path(tempfile.mkdtemp(prefix="bt-e2e-", dir="/tmp"))
    sock = run_dir / "d.sock"
    config = run_dir / "agentd.toml"
    agent_args = ", ".join(_toml_str(a) for a in _AGENT_CMD[1:])
    # mode = "default" makes claude-agent-acp ask before edits even when the
    # user's own Claude settings auto-approve; agents without it ignore it.
    config.write_text(
        f"[profiles.{PROFILE}]\n"
        f"agent_command = {_toml_str(_AGENT_CMD[0])}\n"
        f"agent_args = [{agent_args}]\n"
        'mode = "default"\n'
    )
    log_file = run_dir / "agentd.log"
    proc = subprocess.Popen(
        [
            str(_AGENTD),
            "--socket",
            str(sock),
            "--config",
            str(config),
            "--state-dir",
            str(run_dir / "state"),
            "--log-level",
            "info",
            "--log-file",
            str(log_file),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15.0
        while not sock.exists():
            if proc.poll() is not None:
                pytest.fail(f"blemees-agentd exited early ({proc.returncode}); see {log_file}")
            if time.monotonic() > deadline:
                pytest.fail(f"blemees-agentd socket never appeared; see {log_file}")
            time.sleep(0.05)
        yield str(sock)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        shutil.rmtree(run_dir, ignore_errors=True)


@pytest.fixture
def work_dir() -> Iterator[Path]:
    """Session cwd: a scratch dir, so a permitted tool can't touch the repo."""
    path = Path(tempfile.mkdtemp(prefix="bt-e2e-cwd-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _frames_in(app: BlemeesTuiApp) -> list[dict]:
    return [f for direction, f in app._debug_frames if direction == "in"]


def _seen(app: BlemeesTuiApp, sid: str, ftype: str) -> int:
    return sum(1 for f in _frames_in(app) if f.get("type") == ftype and f.get("session_id") == sid)


def _agent_error(app: BlemeesTuiApp, sid: str) -> dict | None:
    for f in _frames_in(app):
        if f.get("session_id") == sid and f.get("type") in ("session.error", "error"):
            return f
    return None


async def _wait(pilot, pred: Callable[[], bool], *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await pilot.pause(0.1)
    return pred()


async def _wait_for_agent(
    app, pilot, sid: str, pred: Callable[[], bool], *, what: str, timeout: float
):
    """Wait on the agent; skip if it never answers or reports an error.

    Use only for agent-side frames. Once a frame has arrived, the TUI must
    render it, so check the rendering with plain asserts.
    """
    if not await _wait(
        pilot, lambda: pred() or _agent_error(app, sid) is not None, timeout=timeout
    ):
        pytest.skip(f"agent unresponsive waiting for {what} ({timeout}s), authenticated?")
    err = _agent_error(app, sid)
    if err is not None and not pred():
        pytest.skip(f"agent surfaced {err.get('code')}: {err.get('message')} (auth?)")


def _turn_text(sess: SessionState, index: int = -1) -> str:
    return "".join(b.text for b in sess.turns[index].blocks if isinstance(b, TextBlock))


async def _open_session_via_modal(app: BlemeesTuiApp, pilot, cwd: Path) -> str:
    """Ctrl+N → new-session modal → Open; return the new session id."""
    known = set(app.state.sessions)
    await pilot.press("ctrl+n")
    assert await _wait(pilot, lambda: isinstance(app.screen, NewSessionModal), timeout=5.0)
    modal = app.screen
    # The modal fetches profile.list from the daemon; the agent radio fills in.
    assert await _wait(pilot, lambda: bool(modal._selected_agent()), timeout=10.0), (
        "new-session modal never listed the smoke profile's agent"
    )
    assert modal._profile == PROFILE
    modal.query_one("#open-cwd").value = str(cwd)
    await pilot.click("#open")
    assert await _wait(pilot, lambda: set(app.state.sessions) - known, timeout=5.0)
    (sid,) = set(app.state.sessions) - known
    await _wait_for_agent(
        app,
        pilot,
        sid,
        lambda: _seen(app, sid, "session.opened") > 0,
        what="session.opened",
        timeout=60.0,
    )
    assert app.state.active_session_id == sid
    return sid


async def _send_prompt(app: BlemeesTuiApp, pilot, text: str) -> None:
    composer = app.query_one("#composer", ComposerWidget)
    app.action_focus_composer()
    await pilot.pause()
    composer.set_text(text)
    await pilot.press("enter")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_prompt_stream_then_reattach_as_viewer(
    daemon_socket, work_dir, isolated_state_dir, monkeypatch
):
    # Owner TUI: connect, open under the profile, prompt, watch it stream.
    owner = BlemeesTuiApp(socket_override=daemon_socket, profile_override=PROFILE)
    async with owner.run_test(size=(140, 45)) as pilot:
        assert await _wait(pilot, lambda: bool(owner.state.daemon.protocol), timeout=10.0), (
            "no hello_ack from the daemon"
        )
        sid = await _open_session_via_modal(owner, pilot, work_dir)
        sess = owner.state.sessions[sid]
        assert sess.mode == SessionMode.OWNED
        assert sess.backend == PROFILE

        await _send_prompt(owner, pilot, "Reply with exactly the word: pong")
        await _wait_for_agent(
            owner,
            pilot,
            sid,
            lambda: _seen(owner, sid, "session.result") > 0,
            what="session.result",
            timeout=120.0,
        )
        assert _seen(owner, sid, "session.update") > 0, "no streamed session.update frames"
        assert await _wait(pilot, lambda: not sess.turn_active, timeout=5.0), (
            "turn never ended in the TUI; session frames (type, seq): "
            f"{[(f.get('type'), f.get('seq')) for f in _frames_in(owner) if f.get('session_id')]}. "
            "A session.update after session.result (daemon ordering) strands a new turn."
        )
        assert len(sess.turns) == 1
        assert sess.turns[0].user_text == "Reply with exactly the word: pong"
        reply = _turn_text(sess)
        assert "pong" in reply.lower(), f"unexpected reply: {reply!r}"
        chat = owner.query_one("#chat", ChatPaneWidget)
        assert len(chat._turn_widgets) == 1

    # Viewer TUI: fresh local state (no snapshot), attach by id via Ctrl+T.
    viewer_state = Path(tempfile.mkdtemp(prefix="bt-e2e-viewer-"))
    monkeypatch.setenv("XDG_STATE_HOME", str(viewer_state))
    try:
        viewer = BlemeesTuiApp(socket_override=daemon_socket, profile_override=PROFILE)
        async with viewer.run_test(size=(140, 45)) as pilot:
            assert await _wait(pilot, lambda: bool(viewer.state.daemon.protocol), timeout=10.0)
            assert sid not in viewer.state.sessions
            await pilot.press("ctrl+t")
            assert await _wait(pilot, lambda: isinstance(viewer.screen, AttachModal), timeout=5.0)
            # The registry-backed session.list shows the owner's session.
            table = viewer.screen.query_one("#live-sessions")
            assert await _wait(pilot, lambda: table.row_count > 0, timeout=10.0)
            viewer.screen.query_one("#session-id").value = sid
            await pilot.click("#watch")
            assert await _wait(
                pilot, lambda: _seen(viewer, sid, "session.attached") > 0, timeout=10.0
            ), "viewer attach never acked"
            vsess = viewer.state.sessions[sid]
            assert vsess.mode == SessionMode.WATCHING
            assert viewer.state.active_session_id == sid
            # Replay since seq 0 rebuilds the owner's transcript.
            assert await _wait(
                pilot,
                lambda: bool(vsess.turns) and "pong" in _turn_text(vsess).lower(),
                timeout=10.0,
            ), (
                f"replay didn't rebuild the turn: {[_turn_text(vsess, i) for i in range(len(vsess.turns))]}"
            )
            chat = viewer.query_one("#chat", ChatPaneWidget)
            assert chat._banner_state == "watching"
            assert viewer.query_one("#composer-input").disabled
    finally:
        shutil.rmtree(viewer_state, ignore_errors=True)


@pytest.mark.asyncio
async def test_inline_permission_card_round_trip(daemon_socket, work_dir, isolated_state_dir):
    app = BlemeesTuiApp(socket_override=daemon_socket, profile_override=PROFILE)
    async with app.run_test(size=(140, 45)) as pilot:
        assert await _wait(pilot, lambda: bool(app.state.daemon.protocol), timeout=10.0)
        sid = await _open_session_via_modal(app, pilot, work_dir)
        sess = app.state.sessions[sid]

        # "permit" also triggers fake_acp's permission request.
        await _send_prompt(
            app,
            pilot,
            "Use your file-writing tool to create a file named smoke.txt "
            "containing the single word permit. Do nothing else.",
        )
        await _wait_for_agent(
            app,
            pilot,
            sid,
            lambda: (
                _seen(app, sid, "session.request_permission") > 0
                or _seen(app, sid, "session.result") > 0
            ),
            what="session.request_permission",
            timeout=120.0,
        )
        if _seen(app, sid, "session.request_permission") == 0:
            pytest.skip("turn ended without a permission request (tool pre-approved?)")

        # The card renders inline; pick the first allow option.
        chat = app.query_one("#chat", ChatPaneWidget)
        assert await _wait(pilot, lambda: chat._perm_widget is not None, timeout=5.0), (
            "permission card never rendered"
        )
        allow = next(
            (
                i
                for i, o in enumerate(chat._perm_options)
                if str(o.get("kind", "")).startswith("allow")
            ),
            None,
        )
        assert allow is not None, f"no allow option offered: {chat._perm_options}"
        chat.scroll_to_permission()
        await pilot.pause()
        await pilot.click(f"#perm-opt-{allow}")
        assert await _wait(pilot, lambda: sess.pending_permission is None, timeout=5.0)

        await _wait_for_agent(
            app,
            pilot,
            sid,
            lambda: _seen(app, sid, "session.result") > 0,
            what="session.result after permission",
            timeout=120.0,
        )
        assert await _wait(pilot, lambda: chat._perm_widget is None, timeout=5.0)
        assert not sess.turn_active
        result = next(
            f
            for f in _frames_in(app)
            if f.get("type") == "session.result" and f.get("session_id") == sid
        )
        assert result.get("stop_reason") == "end_turn", result
        if _AGENT_CMD[0] == "claude-agent-acp":
            # Only the real agent writes the file; fake_acp just echoes.
            assert (work_dir / "smoke.txt").is_file(), "allowed tool didn't run"

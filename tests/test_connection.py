"""Connection-layer smoke tests — blemees/3 (#1)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
from pathlib import Path

import pytest

from blemees_tui.connection import Connection, ConnectionError_, FatalProtocolError, _BackoffPolicy

HELLO_ACK = {
    "type": "hello_ack",
    "daemon": "blemees-agentd/0.11.0",
    "protocol": "blemees/3",
    "pid": 999,
    "agents": {"claude-agent-acp": "1.0"},
    "profiles": ["default"],
}


@pytest.fixture
def short_tmpdir():
    """AF_UNIX paths are capped at ~104 chars on macOS — pytest's tmp_path
    on a default install blows past that. Use ``/tmp`` directly."""
    d = Path(tempfile.mkdtemp(prefix="bt-"))
    try:
        yield d
    finally:
        for f in d.iterdir():
            with contextlib.suppress(OSError):
                f.unlink()
        with contextlib.suppress(OSError):
            d.rmdir()


async def _serve(socket_path: Path, handle):
    server = await asyncio.start_unix_server(handle, path=str(socket_path))
    return server


def _writeln(writer, frame: dict) -> None:
    writer.write((json.dumps(frame) + "\n").encode("utf-8"))


@pytest.fixture
async def fake_daemon(short_tmpdir):
    """A tiny Unix-socket server that scripts a blemees/3 hello_ack."""
    socket_path = short_tmpdir / "d.sock"
    received: list[dict] = []
    inbound_event = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                line = await reader.readuntil(b"\n")
                frame = json.loads(line.decode("utf-8"))
                received.append(frame)
                inbound_event.set()
                if frame.get("type") == "hello":
                    _writeln(writer, HELLO_ACK)
                    await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

    server = await _serve(socket_path, handle)
    yield socket_path, received, inbound_event
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_handshake_populates_daemon_info(fake_daemon):
    socket_path, received, _ = fake_daemon
    forwarded: list[dict] = []
    conn = Connection(socket_path=str(socket_path), on_frame=forwarded.append)
    await conn.start()
    try:
        for _ in range(50):
            if conn.daemon_info:
                break
            await asyncio.sleep(0.02)
        # The TUI sends `hello` (not the retired `agent.hello`).
        assert any(f.get("type") == "hello" for f in received)
        assert conn.daemon_info.get("protocol") == "blemees/3"
        assert conn.daemon_info.get("agents", {}).get("claude-agent-acp") == "1.0"
        # hello_ack must reach on_frame so the app can populate state.daemon.
        ack_frames = [f for f in forwarded if f.get("type") == "hello_ack"]
        assert ack_frames, "hello_ack was not forwarded to on_frame"
        assert ack_frames[0].get("profiles") == ["default"]
    finally:
        await conn.stop()


@pytest.mark.asyncio
async def test_tracked_viewer_is_restored_on_connect(short_tmpdir):
    """On connect, a tracked watch is reissued as ``session.attach{as:viewer,
    last_seen_seq}``; a ``session.attached`` ack keeps it tracked."""
    socket_path = short_tmpdir / "w.sock"
    received: list[dict] = []

    async def handle(reader, writer):
        try:
            while True:
                line = await reader.readuntil(b"\n")
                frame = json.loads(line.decode("utf-8"))
                received.append(frame)
                if frame.get("type") == "hello":
                    _writeln(writer, HELLO_ACK)
                    await writer.drain()
                elif frame.get("type") == "session.attach":
                    _writeln(
                        writer,
                        {
                            "type": "session.attached",
                            "id": frame.get("id"),
                            "session_id": frame["session_id"],
                            "last_seq": 17,
                        },
                    )
                    await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

    server = await _serve(socket_path, handle)
    try:
        conn = Connection(socket_path=str(socket_path))
        conn.track_watch("sid_w", last_seen_seq=5)
        await conn.start()
        for _ in range(50):
            if any(f.get("type") == "session.attach" for f in received):
                break
            await asyncio.sleep(0.02)
        assert any(
            f.get("type") == "session.attach"
            and f.get("session_id") == "sid_w"
            and f.get("as") == "viewer"
            and f.get("last_seen_seq") == 5
            for f in received
        )
        assert "sid_w" in conn._tracked
        await conn.stop()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_session_unknown_on_restore_untracks_and_forwards(short_tmpdir):
    """When the daemon replies ``error{session_unknown}`` to an attach during
    restore, the connection drops the tracked entry and forwards the error."""
    socket_path = short_tmpdir / "u.sock"
    forwarded: list[dict] = []

    async def handle(reader, writer):
        try:
            while True:
                line = await reader.readuntil(b"\n")
                frame = json.loads(line.decode("utf-8"))
                if frame.get("type") == "hello":
                    _writeln(writer, HELLO_ACK)
                    await writer.drain()
                elif frame.get("type") == "session.attach":
                    _writeln(
                        writer,
                        {
                            "type": "error",
                            "id": frame.get("id"),
                            "session_id": frame["session_id"],
                            "code": "session_unknown",
                            "message": "no such session",
                        },
                    )
                    await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

    server = await _serve(socket_path, handle)
    try:
        conn = Connection(socket_path=str(socket_path), on_frame=forwarded.append)
        conn.track_watch("sid_x", last_seen_seq=99)
        await conn.start()
        for _ in range(100):
            if any(
                f.get("type") == "error" and f.get("code") == "session_unknown" for f in forwarded
            ):
                break
            await asyncio.sleep(0.02)
        assert "sid_x" not in conn._tracked
        assert any(
            f.get("type") == "error"
            and f.get("code") == "session_unknown"
            and f.get("session_id") == "sid_x"
            for f in forwarded
        )
        await conn.stop()
    finally:
        server.close()
        await server.wait_closed()


# profile.info as blemees-agentd sends it (#12): secrets redacted, keys kept.
PROFILE_INFO = {
    "type": "profile.info",
    "name": "mine",
    "source": "dynamic",
    "profile": {
        "name": "mine",
        "agents": {
            "default": {
                "command": "claude-agent-acp",
                "args": [],
                "model": "sonnet",
                "mcp_servers": [{"name": "fs", "command": "fs-mcp", "env": {"T": "<redacted>"}}],
                "env": {"ANTHROPIC_API_KEY": "<redacted>"},
            }
        },
        "permission_policy": {"mode": "relay", "detached": "stall"},
        "notify": {"webhook_url": "<redacted>", "format": "ntfy"},
        "attention": {"triggers": ["permission"]},
    },
}


@pytest.mark.asyncio
async def test_profile_crud_verbs(short_tmpdir):
    """list/create/update/delete/get map to the right frames + ack types (#5/#12)."""
    socket_path = short_tmpdir / "p.sock"
    received: list[dict] = []

    async def handle(reader, writer):
        try:
            while True:
                line = await reader.readuntil(b"\n")
                frame = json.loads(line.decode("utf-8"))
                received.append(frame)
                t = frame.get("type")
                if t == "hello":
                    _writeln(writer, HELLO_ACK)
                elif t == "profile.list":
                    _writeln(
                        writer,
                        {
                            "type": "profiles",
                            "id": frame.get("id"),
                            "profiles": [{"name": "default"}],
                        },
                    )
                elif t == "profile.create":
                    _writeln(
                        writer,
                        {"type": "profile.created", "id": frame.get("id"), "name": frame["name"]},
                    )
                elif t == "profile.delete":
                    _writeln(
                        writer,
                        {"type": "profile.deleted", "id": frame.get("id"), "name": frame["name"]},
                    )
                elif t == "profile.get" and frame["name"] == "mine":
                    _writeln(writer, {**PROFILE_INFO, "id": frame.get("id")})
                elif t == "profile.get":
                    _writeln(
                        writer,
                        {
                            "type": "error",
                            "id": frame.get("id"),
                            "code": "profile_unknown",
                            "message": f"no profile {frame['name']!r}",
                        },
                    )
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

    server = await _serve(socket_path, handle)
    try:
        conn = Connection(socket_path=str(socket_path))
        await conn.start()
        for _ in range(50):
            if conn.daemon_info:
                break
            await asyncio.sleep(0.02)
        profiles = await conn.list_profiles()
        assert profiles == [{"name": "default"}]
        created = await conn.create_profile("mine", {"agent": {"agent_command": "x"}})
        assert created["name"] == "mine"
        deleted = await conn.delete_profile("mine")
        assert deleted["name"] == "mine"
        info = await conn.get_profile("mine")
        assert info["source"] == "dynamic"
        assert info["profile"] == PROFILE_INFO["profile"]
        get_frame = next(f for f in received if f.get("type") == "profile.get")
        assert get_frame["name"] == "mine"
        with pytest.raises(ConnectionError_, match="profile_unknown"):
            await conn.get_profile("nope")
        sent_types = [f.get("type") for f in received]
        assert "profile.create" in sent_types and "profile.delete" in sent_types
        await conn.stop()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_respond_permission_sends_response_frame(fake_daemon):
    socket_path, received, _ = fake_daemon
    conn = Connection(socket_path=str(socket_path))
    await conn.start()
    try:
        for _ in range(50):
            if conn.daemon_info:
                break
            await asyncio.sleep(0.02)
        await conn.respond_permission("s1", "perm_1", outcome="selected", option_id="allow")
        for _ in range(50):
            if any(f.get("type") == "session.permission_response" for f in received):
                break
            await asyncio.sleep(0.02)
        frame = next(f for f in received if f.get("type") == "session.permission_response")
        assert frame["session_id"] == "s1"
        assert frame["request_id"] == "perm_1"
        assert frame["outcome"] == "selected"
        assert frame["option_id"] == "allow"
    finally:
        await conn.stop()


@pytest.mark.asyncio
async def test_fatal_protocol_mismatch_stops_supervisor(short_tmpdir):
    socket_path = short_tmpdir / "b.sock"

    async def handle(reader, writer):
        try:
            await reader.readuntil(b"\n")
            _writeln(
                writer,
                {"type": "error", "code": "protocol_mismatch", "message": "expected blemees/3"},
            )
            await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

    server = await _serve(socket_path, handle)
    try:
        statuses: list[str] = []
        conn = Connection(
            socket_path=str(socket_path),
            on_status_change=lambda s: statuses.append(s.state),
        )
        await conn.start()
        for _ in range(50):
            if "fatal" in statuses:
                break
            await asyncio.sleep(0.02)
        assert "fatal" in statuses
        await conn.stop()
    finally:
        server.close()
        await server.wait_closed()
    assert issubclass(FatalProtocolError, RuntimeError)


@pytest.mark.asyncio
async def test_large_frames_survive_and_dispatch(short_tmpdir):
    # Frames far beyond asyncio's 64 KiB default limit are routine agent
    # output (big tool results) — they must dispatch, not kill the reader (#14).
    socket_path = short_tmpdir / "d.sock"
    big_text = "x" * (512 * 1024)

    async def handle(reader, writer):
        try:
            while True:
                line = await reader.readuntil(b"\n")
                frame = json.loads(line.decode("utf-8"))
                if frame.get("type") == "hello":
                    _writeln(writer, HELLO_ACK)
                    _writeln(
                        writer,
                        {"type": "session.update", "session_id": "s1", "seq": 1, "big": big_text},
                    )
                    await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

    server = await _serve(socket_path, handle)
    forwarded: list[dict] = []
    conn = Connection(socket_path=str(socket_path), on_frame=forwarded.append)
    await conn.start()
    try:
        for _ in range(100):
            if any(f.get("big") for f in forwarded):
                break
            await asyncio.sleep(0.02)
        big = [f for f in forwarded if f.get("type") == "session.update"]
        assert big and big[0]["big"] == big_text
        assert conn.status.state == "connected"
    finally:
        await conn.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_undecodable_frame_reconnects_instead_of_killing_supervisor(short_tmpdir):
    # A garbage line used to escape the reader's except set and kill the
    # supervisor with status stuck on "connected" (#14). It must reconnect.
    socket_path = short_tmpdir / "d.sock"
    hellos = 0

    async def handle(reader, writer):
        nonlocal hellos
        try:
            while True:
                line = await reader.readuntil(b"\n")
                frame = json.loads(line.decode("utf-8"))
                if frame.get("type") == "hello":
                    hellos += 1
                    _writeln(writer, HELLO_ACK)
                    if hellos == 1:
                        writer.write(b"this is not json\n")
                    await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

    server = await _serve(socket_path, handle)
    conn = Connection(socket_path=str(socket_path), on_frame=lambda f: None)
    await conn.start()
    try:
        # The garbage frame forces a reconnect: a second hello arrives and the
        # client settles back into "connected" — supervisor alive throughout.
        for _ in range(200):
            if hellos >= 2 and conn.status.state == "connected":
                break
            await asyncio.sleep(0.02)
        assert hellos >= 2
        assert conn.status.state == "connected"
    finally:
        await conn.stop()
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# Connection hardening (#20)
# ---------------------------------------------------------------------------


def _fast_backoff(conn: Connection) -> None:
    conn._policy = _BackoffPolicy(initial=0.05, cap=0.05, jitter=0.0)


async def _scripted_daemon(socket_path: Path, *, ack_hello=True, answer_pings=True):
    """Daemon double: counts hellos; optionally stays silent on hello/ping."""
    seen: dict[str, int] = {"hello": 0, "ping": 0}
    received: list[dict] = []

    async def handle(reader, writer):
        try:
            while True:
                line = await reader.readuntil(b"\n")
                frame = json.loads(line.decode("utf-8"))
                received.append(frame)
                t = frame.get("type")
                if t in seen:
                    seen[t] += 1
                if t == "hello" and ack_hello:
                    _writeln(writer, HELLO_ACK)
                elif t == "ping" and answer_pings:
                    _writeln(writer, {"type": "pong", "id": frame.get("id")})
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

    server = await _serve(socket_path, handle)
    return server, seen, received


async def _until(pred, *, tries=200, step=0.02) -> bool:
    for _ in range(tries):
        if pred():
            return True
        await asyncio.sleep(step)
    return pred()


@pytest.mark.asyncio
async def test_silent_hello_times_out_and_retries(short_tmpdir):
    # A daemon that accepts but never answers hello used to hang the
    # supervisor forever with no retry.
    server, seen, _ = await _scripted_daemon(short_tmpdir / "h.sock", ack_hello=False)
    errors: list[str] = []
    conn = Connection(
        socket_path=str(short_tmpdir / "h.sock"),
        handshake_timeout=0.1,
        on_status_change=lambda s: errors.append(s.last_error),
    )
    _fast_backoff(conn)
    await conn.start()
    try:
        assert await _until(lambda: seen["hello"] >= 2)
        assert any("hello_ack" in e for e in errors)
        assert conn.status.state != "connected"
    finally:
        await conn.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_missing_pong_forces_reconnect(short_tmpdir):
    # Half-open link: hello works, then nothing comes back. The pong
    # deadline must tear the link down and reconnect.
    server, seen, _ = await _scripted_daemon(short_tmpdir / "p.sock", answer_pings=False)
    errors: list[str] = []
    conn = Connection(
        socket_path=str(short_tmpdir / "p.sock"),
        ping_interval=0.05,
        idle_threshold=0.0,
        pong_timeout=0.1,
        on_status_change=lambda s: errors.append(s.last_error),
    )
    _fast_backoff(conn)
    await conn.start()
    try:
        assert await _until(lambda: seen["hello"] >= 2)
        assert any("pong" in e for e in errors)
    finally:
        await conn.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_answered_pings_keep_the_link(short_tmpdir):
    server, seen, received = await _scripted_daemon(short_tmpdir / "k.sock")
    conn = Connection(
        socket_path=str(short_tmpdir / "k.sock"),
        ping_interval=0.05,
        idle_threshold=0.0,
        pong_timeout=0.2,
    )
    _fast_backoff(conn)
    await conn.start()
    try:
        assert await _until(lambda: seen["ping"] >= 3)
        assert seen["hello"] == 1
        assert conn.status.state == "connected"
        # Pings carry an id so the pong can be correlated.
        assert all(f.get("id") for f in received if f.get("type") == "ping")
    finally:
        await conn.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_status_flips_to_reconnecting_as_soon_as_reader_ends(short_tmpdir):
    # After a clean EOF the first backoff window used to run with the
    # status still "connected".
    socket_path = short_tmpdir / "e.sock"
    hangup = asyncio.Event()

    async def handle(reader, writer):
        await reader.readuntil(b"\n")
        _writeln(writer, HELLO_ACK)
        await writer.drain()
        await hangup.wait()
        writer.close()

    server = await _serve(socket_path, handle)
    conn = Connection(socket_path=str(socket_path))
    # Long backoff: any "reconnecting" we observe must come from the
    # reader-end path, not from the next loop iteration.
    conn._policy = _BackoffPolicy(initial=30.0, cap=30.0, jitter=0.0)
    await conn.start()
    try:
        assert await _until(lambda: conn.status.state == "connected")
        hangup.set()
        assert await _until(lambda: conn.status.state == "reconnecting", tries=25)
        assert conn.status.next_in_ms > 0
    finally:
        await conn.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_link_death_during_restore_keeps_session_tracked(short_tmpdir):
    # The daemon goes silent while a tracked session is being resumed. The
    # pong deadline fails the in-flight open; that is a dead link, not a
    # session_unknown, so the session must stay tracked for the next try.
    # The open is still pending when the ping goes out, so the bound is
    # request_timeout, not pong_timeout.
    server, seen, _ = await _scripted_daemon(short_tmpdir / "r.sock", answer_pings=False)
    errors: list[str] = []
    conn = Connection(
        socket_path=str(short_tmpdir / "r.sock"),
        ping_interval=0.05,
        idle_threshold=0.0,
        pong_timeout=0.1,
        request_timeout=0.3,
        on_status_change=lambda s: errors.append(s.last_error),
    )
    _fast_backoff(conn)
    conn.track_owned("sid_r", agent="claude", last_seen_seq=3)
    await conn.start()
    try:
        assert await _until(lambda: seen["hello"] >= 2)
        assert "sid_r" in conn._tracked
        assert any("pending request within 0.3s" in e for e in errors)
    finally:
        await conn.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_eof_during_restore_does_not_hang_or_untrack(short_tmpdir):
    # The daemon hangs up while a resume is in flight. The open must fail
    # as a lost link (session stays tracked) and the supervisor must retry.
    socket_path = short_tmpdir / "x.sock"
    hellos = 0

    async def handle(reader, writer):
        nonlocal hellos
        try:
            while True:
                line = await reader.readuntil(b"\n")
                frame = json.loads(line.decode("utf-8"))
                if frame.get("type") == "hello":
                    hellos += 1
                    _writeln(writer, HELLO_ACK)
                    await writer.drain()
                elif frame.get("type") == "session.open":
                    break
        except asyncio.IncompleteReadError:
            # The client hung up first; nothing to do but close our side.
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

    server = await _serve(socket_path, handle)
    conn = Connection(socket_path=str(socket_path))
    _fast_backoff(conn)
    conn.track_owned("sid_e", agent="claude")
    await conn.start()
    try:
        assert await _until(lambda: hellos >= 2)
        assert "sid_e" in conn._tracked
    finally:
        await conn.stop()
        server.close()
        await server.wait_closed()


async def _serial_daemon(socket_path: Path, *, open_delay: float):
    """Daemon double that, like blemees-agentd, finishes each request before
    it reads the next frame — a ping sent during a slow open waits behind it."""
    seen: dict[str, int] = {"hello": 0, "ping": 0}

    async def handle(reader, writer):
        try:
            while True:
                line = await reader.readuntil(b"\n")
                frame = json.loads(line.decode("utf-8"))
                t = frame.get("type")
                if t in seen:
                    seen[t] += 1
                if t == "hello":
                    _writeln(writer, HELLO_ACK)
                elif t == "ping":
                    _writeln(writer, {"type": "pong", "id": frame.get("id")})
                elif t == "session.open":
                    await asyncio.sleep(open_delay)
                    _writeln(
                        writer,
                        {
                            "type": "session.opened",
                            "id": frame.get("id"),
                            "session_id": frame.get("session_id"),
                        },
                    )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

    server = await _serve(socket_path, handle)
    return server, seen


@pytest.mark.asyncio
async def test_slow_open_does_not_trip_the_pong_deadline(short_tmpdir):
    # A backend that takes longer than pong_timeout to start keeps the
    # daemon from reading the ping. That is a busy daemon, not a dead link:
    # the open must succeed on the same connection.
    server, seen = await _serial_daemon(short_tmpdir / "s.sock", open_delay=0.5)
    conn = Connection(
        socket_path=str(short_tmpdir / "s.sock"),
        ping_interval=0.05,
        idle_threshold=0.0,
        pong_timeout=0.1,
        request_timeout=2.0,
    )
    _fast_backoff(conn)
    await conn.start()
    try:
        assert await _until(lambda: conn.status.state == "connected")
        reply = await asyncio.wait_for(conn.open_session("sid_s", agent="claude"), timeout=3.0)
        assert reply["type"] == "session.opened"
        assert seen["ping"] >= 1
        assert seen["hello"] == 1
        assert conn.status.state == "connected"
    finally:
        await conn.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_open_written_just_before_the_ping_gets_the_request_timeout(short_tmpdir):
    # An open that wakes in the same loop tick as the ping loop reaches the
    # wire before the ping. It is ahead of the ping in the daemon's queue,
    # so the bound must be request_timeout even though it was not pending
    # yet when the ping loop decided to ping.
    server, seen = await _serial_daemon(short_tmpdir / "c.sock", open_delay=0.3)
    opens: list[asyncio.Task] = []

    async def open_on_ping_tick():
        await asyncio.sleep(0.02)
        return await conn.open_session("sid_c", agent="claude")

    def on_status(status):
        if status.state == "connected" and not opens:
            opens.append(asyncio.ensure_future(open_on_ping_tick()))

    conn = Connection(
        socket_path=str(short_tmpdir / "c.sock"),
        ping_interval=0.02,
        idle_threshold=0.0,
        pong_timeout=0.05,
        request_timeout=1.0,
        on_status_change=on_status,
    )
    _fast_backoff(conn)
    await conn.start()
    try:
        assert await _until(lambda: bool(opens))
        reply = await asyncio.wait_for(opens[0], timeout=3.0)
        assert reply["type"] == "session.opened"
        assert seen["hello"] == 1
    finally:
        await conn.stop()
        server.close()
        await server.wait_closed()

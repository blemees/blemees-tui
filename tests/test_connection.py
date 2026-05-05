"""Connection-layer smoke tests (spec §17.1)."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from blemees_tui.connection import Connection, FatalProtocolError


@pytest.fixture
def short_tmpdir():
    """AF_UNIX paths are capped at ~104 chars on macOS — pytest's tmp_path
    on a default install blows past that. Use ``/tmp`` directly."""
    d = Path(tempfile.mkdtemp(prefix="bt-"))
    try:
        yield d
    finally:
        for f in d.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        try:
            d.rmdir()
        except OSError:
            pass


@pytest.fixture
async def fake_daemon(short_tmpdir):
    """Spin up a tiny Unix-socket server that scripts a hello_ack reply."""
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
                if frame.get("type") == "blemeesd.hello":
                    ack = {
                        "type": "blemeesd.hello_ack",
                        "daemon": "blemees-agentd/0.9.2",
                        "protocol": "blemees/2",
                        "pid": 999,
                        "backends": {"claude": "2.1"},
                    }
                    writer.write((json.dumps(ack) + "\n").encode("utf-8"))
                    await writer.drain()
        except asyncio.IncompleteReadError:
            pass

    server = await asyncio.start_unix_server(handle, path=str(socket_path))
    yield socket_path, received, inbound_event
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_handshake_populates_daemon_info(fake_daemon):
    socket_path, received, inbound_event = fake_daemon
    received_frames: list[dict] = []
    conn = Connection(
        socket_path=str(socket_path),
        on_frame=lambda f: received_frames.append(f),
    )
    await conn.start()
    try:
        # Wait until at least the hello has reached the fake server.
        for _ in range(50):
            if any(f.get("type") == "blemeesd.hello" for f in received):
                break
            await asyncio.sleep(0.02)
        # And until the connection picked up the ack.
        for _ in range(50):
            if conn.daemon_info:
                break
            await asyncio.sleep(0.02)
        assert conn.daemon_info.get("backends", {}).get("claude") == "2.1"
        assert any(f.get("type") == "blemeesd.hello" for f in received)
        # Regression: the hello_ack must also reach on_frame so the app can
        # populate state.daemon (the footer otherwise renders "daemon ?" /
        # "no backends" even after the green-dot connect).
        ack_frames = [f for f in received_frames if f.get("type") == "blemeesd.hello_ack"]
        assert ack_frames, "hello_ack was not forwarded to on_frame"
        assert ack_frames[0].get("backends", {}).get("claude") == "2.1"
    finally:
        await conn.stop()


@pytest.mark.asyncio
async def test_tracked_watch_is_restored_on_connect(short_tmpdir):
    """Spec §6.3 / §8.7: on connect, a tracked watch is reissued via
    ``blemeesd.watch{last_seen_seq:<stored>}``. A successful ``watching``
    ack keeps it tracked."""
    socket_path = short_tmpdir / "w.sock"
    received: list[dict] = []

    async def handle(reader, writer):
        try:
            while True:
                line = await reader.readuntil(b"\n")
                frame = json.loads(line.decode("utf-8"))
                received.append(frame)
                if frame.get("type") == "blemeesd.hello":
                    writer.write((json.dumps({
                        "type": "blemeesd.hello_ack",
                        "daemon": "blemees-agentd/0.9.2",
                        "protocol": "blemees/2",
                        "pid": 1,
                        "backends": {"claude": "2.1"},
                    }) + "\n").encode())
                    await writer.drain()
                elif frame.get("type") == "blemeesd.watch":
                    writer.write((json.dumps({
                        "type": "blemeesd.watching",
                        "id": frame.get("id"),
                        "session_id": frame["session_id"],
                        "last_seq": 17,
                    }) + "\n").encode())
                    await writer.drain()
        except asyncio.IncompleteReadError:
            pass

    server = await asyncio.start_unix_server(handle, path=str(socket_path))
    try:
        conn = Connection(socket_path=str(socket_path))
        conn.track_watch("sid_w", last_seen_seq=5)
        await conn.start()
        for _ in range(50):
            if any(f.get("type") == "blemeesd.watch" for f in received):
                break
            await asyncio.sleep(0.02)
        assert any(
            f.get("type") == "blemeesd.watch"
            and f.get("session_id") == "sid_w"
            and f.get("last_seen_seq") == 5
            for f in received
        )
        # Tracked still present after success.
        assert "sid_w" in conn._tracked
        await conn.stop()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_session_unknown_on_restore_untracks_and_forwards(short_tmpdir):
    """Spec §6.3, §15: when the daemon replies ``session_unknown`` to a
    ``watch`` during restore, the connection drops the tracked entry and
    forwards the error frame upstream so the app can archive."""
    socket_path = short_tmpdir / "u.sock"
    forwarded: list[dict] = []

    async def handle(reader, writer):
        try:
            while True:
                line = await reader.readuntil(b"\n")
                frame = json.loads(line.decode("utf-8"))
                if frame.get("type") == "blemeesd.hello":
                    writer.write((json.dumps({
                        "type": "blemeesd.hello_ack",
                        "daemon": "blemees-agentd/0.9.2",
                        "protocol": "blemees/2",
                        "pid": 1,
                        "backends": {"claude": "2.1"},
                    }) + "\n").encode())
                    await writer.drain()
                elif frame.get("type") == "blemeesd.watch":
                    writer.write((json.dumps({
                        "type": "blemeesd.error",
                        "id": frame.get("id"),
                        "session_id": frame["session_id"],
                        "code": "session_unknown",
                        "message": "no such session",
                    }) + "\n").encode())
                    await writer.drain()
        except asyncio.IncompleteReadError:
            pass

    server = await asyncio.start_unix_server(handle, path=str(socket_path))
    try:
        conn = Connection(
            socket_path=str(socket_path),
            on_frame=lambda f: forwarded.append(f),
        )
        conn.track_watch("sid_x", last_seen_seq=99)
        await conn.start()
        for _ in range(100):
            if any(
                f.get("type") == "blemeesd.error"
                and f.get("code") == "session_unknown"
                for f in forwarded
            ):
                break
            await asyncio.sleep(0.02)
        # Tracked entry dropped during restore.
        assert "sid_x" not in conn._tracked
        # Error frame was forwarded — app can archive.
        assert any(
            f.get("type") == "blemeesd.error"
            and f.get("code") == "session_unknown"
            and f.get("session_id") == "sid_x"
            for f in forwarded
        )
        await conn.stop()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_fatal_protocol_mismatch_stops_supervisor(short_tmpdir):
    socket_path = short_tmpdir / "b.sock"

    async def handle(reader, writer):
        try:
            await reader.readuntil(b"\n")
            err = {"type": "blemeesd.error", "code": "protocol_mismatch", "message": "blemees/3"}
            writer.write((json.dumps(err) + "\n").encode("utf-8"))
            await writer.drain()
        except asyncio.IncompleteReadError:
            pass

    server = await asyncio.start_unix_server(handle, path=str(socket_path))
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
    # Sanity: FatalProtocolError class is exported and exception-shaped.
    assert issubclass(FatalProtocolError, RuntimeError)

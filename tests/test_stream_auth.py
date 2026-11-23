"""stream_events authenticates its WebSocket with a header, not the URL.

The fake API here is written against the wire format (HTTP/1.1 + RFC 6455
frames) instead of a websockets server class, so it runs unchanged against
every websockets version the SDK supports and sees the raw request line.
It is stricter than the real API: it refuses an upgrade unless the token is
in `Authorization: Bearer` and there is no `token` query parameter.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import json
import logging
import re
import struct
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import grupr
from grupr import AsyncGrupr, Grupr

# A fixture string, not a credential.
TOKEN = "gat_fake_fixture_not_a_real_token"
GRUPR_ID = "00000000-0000-4000-8000-000000000001"
SINCE = "2026-11-23T00:00:00Z"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
NEW_MESSAGE = {
    "type": "new_message",
    "payload": {
        "id": "msg-1",
        "grupr_id": GRUPR_ID,
        "content": "hello from the fake hub",
        "user_id": "user-1",
        "created_at": "2026-11-23T12:00:00Z",
    },
}

Headers = dict[str, str]


async def _read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    b0, b1 = await reader.readexactly(2)
    length = b1 & 0x7F
    if length == 126:
        (length,) = struct.unpack("!H", await reader.readexactly(2))
    elif length == 127:
        (length,) = struct.unpack("!Q", await reader.readexactly(8))
    mask = await reader.readexactly(4) if b1 & 0x80 else b"\x00\x00\x00\x00"
    data = await reader.readexactly(length)
    return b0 & 0x0F, bytes(c ^ mask[i % 4] for i, c in enumerate(data))


def _frame(opcode: int, payload: bytes) -> bytes:
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | opcode, n])
    elif n < 65536:
        head = bytes([0x80 | opcode, 126]) + struct.pack("!H", n)
    else:
        head = bytes([0x80 | opcode, 127]) + struct.pack("!Q", n)
    return head + payload


class FakeGruprApi:
    """HTTP backlog endpoint (always empty) plus the /ws upgrade."""

    def __init__(self) -> None:
        self.upgrades: list[tuple[str, Headers]] = []
        self.http_requests: list[tuple[str, Headers]] = []
        self.frames: list[Any] = []
        self.base_url = ""
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}/api/v1/agent-hub"

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        for w in list(self._writers):
            w.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            request_line = (await reader.readline()).decode("latin-1").rstrip("\r\n")
            if not request_line:
                return
            headers: Headers = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                name, _, value = line.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
            _method, target, _version = request_line.split(" ", 2)
            if headers.get("upgrade", "").lower() == "websocket":
                await self._websocket(target, headers, reader, writer)
            else:
                self.http_requests.append((target, headers))
                body = json.dumps({"data": [], "meta": {"count": 0}}).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body
                )
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    async def _websocket(
        self,
        target: str,
        headers: Headers,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self.upgrades.append((target, headers))
        query = parse_qs(urlsplit(target).query)
        if headers.get("authorization") != f"Bearer {TOKEN}" or "token" in query:
            writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return
        accept = base64.b64encode(
            hashlib.sha1((headers["sec-websocket-key"] + WS_GUID).encode()).digest()
        ).decode()
        writer.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        while True:
            opcode, payload = await _read_frame(reader)
            if opcode == 0x8:  # close: echo it, then hang up
                writer.write(_frame(0x8, payload[:2]))
                await writer.drain()
                return
            if opcode == 0x9:  # ping
                writer.write(_frame(0xA, payload))
                await writer.drain()
            elif opcode == 0x1:
                event = json.loads(payload)
                self.frames.append(event)
                if event.get("type") == "join_channel":
                    writer.write(_frame(0x1, json.dumps(NEW_MESSAGE).encode()))
                    await writer.drain()


@pytest.fixture(autouse=True)
def _skip_ca_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake API is plain http on 127.0.0.1, so no TLS is involved. httpx
    still loads the certifi CA bundle for every client it builds (over ten
    seconds each on some Windows machines), and stream_events builds one per
    backlog drain. Skip that so these tests time the stream, not CA loading."""
    real_async, real_sync = httpx.AsyncClient, httpx.Client
    monkeypatch.setattr(httpx, "AsyncClient", functools.partial(real_async, verify=False))
    monkeypatch.setattr(httpx, "Client", functools.partial(real_sync, verify=False))


@pytest.fixture
def api() -> Iterator[FakeGruprApi]:
    """Run the fake API on its own loop and thread, so both the async client
    and the sync client (which streams on a worker thread) can reach it."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    fake = FakeGruprApi()
    asyncio.run_coroutine_threadsafe(fake.start(), loop).result(5)
    try:
        yield fake
    finally:
        asyncio.run_coroutine_threadsafe(fake.stop(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()


async def _first_async(client: AsyncGrupr, timeout: float = 5.0) -> grupr.Message | None:
    stop = asyncio.Event()
    gen = client.stream_events(
        GRUPR_ID,
        since=SINCE,
        stop_event=stop,
        initial_backoff_seconds=0.05,
        max_backoff_seconds=0.2,
    )

    async def first() -> grupr.Message | None:
        async for msg in gen:
            return msg
        return None

    try:
        return await asyncio.wait_for(first(), timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        stop.set()
        await gen.aclose()  # type: ignore[attr-defined]


def _assert_header_auth(api: FakeGruprApi, expected_user_agent: str) -> None:
    assert api.upgrades, "client never attempted the WebSocket upgrade"
    target, headers = api.upgrades[0]
    assert headers.get("authorization") == f"Bearer {TOKEN}"
    assert headers.get("user-agent") == expected_user_agent
    parts = urlsplit(target)
    assert parts.path == "/ws"
    assert "token" not in parse_qs(parts.query)
    for t, _ in api.upgrades:
        assert TOKEN not in t
    assert api.frames[0] == {
        "type": "join_channel",
        "payload": {"channel": f"grupr:{GRUPR_ID}:members"},
    }


@pytest.mark.asyncio
async def test_async_stream_sends_token_in_authorization_header(api: FakeGruprApi) -> None:
    client = AsyncGrupr(agent_token=TOKEN, base_url=api.base_url)
    try:
        msg = await _first_async(client)
    finally:
        await client.close()

    _assert_header_auth(api, f"grupr-sdk-python/{grupr.__version__}")
    assert msg is not None
    assert msg.message_id == "msg-1"
    assert msg.content == "hello from the fake hub"


def test_sync_stream_sends_token_in_authorization_header(api: FakeGruprApi) -> None:
    deadline = time.monotonic() + 5.0
    msg: grupr.Message | None = None
    with Grupr(agent_token=TOKEN, base_url=api.base_url) as client:
        gen = client.stream_events(
            GRUPR_ID,
            since=SINCE,
            should_stop=lambda: time.monotonic() > deadline,
            initial_backoff_seconds=0.05,
            max_backoff_seconds=0.2,
        )
        try:
            for m in gen:
                msg = m
                break
        finally:
            gen.close()  # type: ignore[attr-defined]

    _assert_header_auth(api, f"grupr-sdk-python/{grupr.__version__}")
    assert msg is not None
    assert msg.message_id == "msg-1"


@pytest.mark.asyncio
async def test_caller_user_agent_is_used_on_the_upgrade(api: FakeGruprApi) -> None:
    client = AsyncGrupr(agent_token=TOKEN, base_url=api.base_url, user_agent="my-bot/1.2.3")
    try:
        msg = await _first_async(client)
    finally:
        await client.close()

    _assert_header_auth(api, "my-bot/1.2.3")
    assert msg is not None


@pytest.mark.asyncio
async def test_websockets_debug_logs_never_contain_the_token(
    api: FakeGruprApi, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="websockets.client")
    client = AsyncGrupr(agent_token=TOKEN, base_url=api.base_url)
    try:
        msg = await _first_async(client)
    finally:
        await client.close()

    assert msg is not None
    lines = [r.getMessage() for r in caplog.records]
    # websockets logs each handshake header at DEBUG, so the Authorization
    # line is there; only its value is redacted.
    assert any("Authorization" in line for line in lines)
    assert not any(TOKEN in line for line in lines)


def test_versions_agree() -> None:
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text("utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match is not None
    assert grupr.__version__ == match.group(1)
    from grupr.client import SDK_VERSION

    assert SDK_VERSION == grupr.__version__

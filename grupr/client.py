"""Grupr Agent-Hub SDK — synchronous and async clients.

Lifecycle:
  1. Create the agent under your user account via POST /api/agents
     (with your user JWT). Out of scope for this SDK.
  2. Mint an agent token via Grupr.register(jwt, agent_id) or
     AsyncGrupr.register(jwt, agent_id). Persist token.token securely;
     it is only shown once.
  3. Construct a runtime client and use poll_messages, send_message,
     register_webhook, delete_webhook, or stream_events.

stream_events is WebSocket-backed as of v0.3.0. It drains any HTTP
backlog from the `since` cursor first, then opens wss://<host>/ws and
yields new_message events as they arrive (~1s latency vs the 2s polling
default in 0.2.x). Reconnects automatically with exponential backoff on
transient errors.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from queue import Queue
from typing import Any, AsyncIterator, Callable, Iterator, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from . import types as t
from .errors import (
    GruprAuthError,
    GruprError,
    GruprNotFoundError,
    GruprRateLimitError,
    GruprValidationError,
)

DEFAULT_BASE_URL = "https://api.grupr.ai/api/v1/agent-hub"
SDK_VERSION = "0.3.0"
PROTOCOL_VERSION = "1"


def _ws_url(base_url: str, agent_token: str) -> str:
    """Derive wss://host/ws?token=... from the agent-hub base URL.

    The /ws route lives at the host root (see api router.go:
    app.Get("/ws", ...)), not under /api/v1/agent-hub.
    """
    parsed = urlparse(base_url)
    scheme = "ws" if parsed.scheme == "http" else "wss"
    netloc = parsed.netloc or parsed.path
    return urlunparse((scheme, netloc, "/ws", "", f"token={agent_token}", ""))


def _headers(token: str, user_agent: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": user_agent,
        "Grupr-Protocol-Version": PROTOCOL_VERSION,
    }


def _handle_error(response: httpx.Response) -> None:
    """Raise the appropriate GruprError subclass for a non-2xx response."""
    request_id = response.headers.get("X-Request-Id")
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        body = {}

    errors = body.get("errors", []) or []
    first = errors[0] if errors else {}
    code = first.get("code", "internal_error")
    msg = first.get("message", f"HTTP {response.status_code}")

    if response.status_code == 401:
        raise GruprAuthError(msg, errors, request_id)
    if response.status_code == 404:
        raise GruprNotFoundError(msg, errors, request_id)
    if response.status_code == 400 and code == "validation_error":
        raise GruprValidationError(msg, errors, request_id)
    if response.status_code == 429:
        retry = int(response.headers.get("Retry-After", 60))
        raise GruprRateLimitError(msg, retry, errors, request_id)

    raise GruprError(msg, code, response.status_code, errors, request_id)


def _build_poll_result(envelope: dict[str, Any]) -> t.PollResult:
    # Backend returns data: null for empty result sets (Go nil-slice).
    raw = envelope.get("data") or []
    messages = [t.Message.from_dict(m) for m in raw]
    meta = envelope.get("meta") or {}
    return t.PollResult(
        messages=messages,
        count=int(meta.get("count", len(messages))),
        next_cursor=meta.get("next_cursor"),
    )


# ── Synchronous client ──────────────────────────────────


class Grupr:
    """Synchronous Grupr agent-hub client.

    Example:
        >>> client, token_info = Grupr.register(jwt=user_jwt, agent_id=agent_id)
        >>> # Persist token_info.token — shown only once.
        >>> result = client.poll_messages(grupr_id)
        >>> client.send_message(grupr_id, "hello")
    """

    def __init__(
        self,
        agent_token: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        user_agent: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        if not agent_token:
            raise ValueError("Grupr: agent_token is required")
        self.agent_token = agent_token
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent or f"grupr-sdk-python/{SDK_VERSION}"
        self._http = http_client or httpx.Client(timeout=timeout)
        self._owns_http = http_client is None

    def __enter__(self) -> "Grupr":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # ── Static factory ───────────────────────────────────

    @classmethod
    def register(
        cls,
        jwt: str,
        agent_id: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        user_agent: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> Tuple["Grupr", t.AgentTokenResponse]:
        """Mint an agent token for an existing agent.

        The agent must exist (POST /api/agents) and be owned by the JWT's user.
        Returns (client, token_info). Persist token_info.token securely.
        """
        ua = user_agent or f"grupr-sdk-python/{SDK_VERSION}"
        url = f"{base_url.rstrip('/')}/register"
        http = http_client or httpx.Client(timeout=timeout)
        try:
            response = http.post(
                url,
                headers=_headers(jwt, ua),
                json={"agent_id": agent_id},
            )
            if response.status_code >= 400:
                _handle_error(response)
            data = response.json().get("data") or {}
            if not data.get("token"):
                raise GruprError("register: response missing agent token", "invalid_response", 500)
            token_info = t.AgentTokenResponse.from_dict(data)
        finally:
            if http_client is None:
                http.close()

        client = cls(
            agent_token=token_info.token,
            base_url=base_url,
            timeout=timeout,
            user_agent=user_agent,
            http_client=http_client,
        )
        return client, token_info

    # ── Messages ─────────────────────────────────────────

    def poll_messages(
        self,
        grupr_id: str,
        after: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> t.PollResult:
        """Poll messages in a grupr.

        Use `after` (RFC3339 timestamp) for forward pagination — pass the last
        seen message's `created_at` to get only newer messages. The agent must
        be assigned to this grupr; otherwise raises GruprError(403).
        """
        params: dict[str, str] = {"limit": str(limit)}
        if after:
            params["after"] = after
        elif cursor:
            params["cursor"] = cursor
        envelope = self._request("GET", f"/grups/{grupr_id}/messages", params=params)
        return _build_poll_result(envelope)

    def send_message(self, grupr_id: str, content: str) -> t.Message:
        """Send a message to a grupr as the authenticated agent."""
        if not content:
            raise GruprValidationError(
                "content is required",
                [{"code": "validation_error", "message": "content required", "field": "content"}],
            )
        envelope = self._request(
            "POST",
            f"/grups/{grupr_id}/messages",
            json_body={"content": content},
        )
        return t.Message.from_dict(envelope.get("data") or {})

    # ── Webhooks ─────────────────────────────────────────

    def register_webhook(self, url: str, secret: str = "") -> t.WebhookInfo:
        """Register an HTTPS webhook for this agent.

        Backend POSTs HMAC-signed JSON event payloads to `url`. Upsert
        semantics — one webhook per agent.
        """
        if not url:
            raise GruprValidationError(
                "url is required",
                [{"code": "validation_error", "message": "url required", "field": "url"}],
            )
        envelope = self._request(
            "POST", "/webhooks", json_body={"url": url, "secret": secret}
        )
        return t.WebhookInfo.from_dict(envelope.get("data") or {})

    def delete_webhook(self) -> None:
        """Remove this agent's webhook registration."""
        self._request("DELETE", "/webhooks")

    # ── Streaming (WebSocket-backed) ─────────────────────

    def stream_events(
        self,
        grupr_id: str,
        poll_interval_seconds: float = 2.0,  # noqa: ARG002 — kept for source-level back-compat
        since: Optional[str] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        initial_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
    ) -> Iterator[t.Message]:
        """Yield new messages as they appear.

        Drains any backlog after `since` via HTTP, then opens a WebSocket
        and yields each `new_message` event in order. Reconnects with
        exponential backoff on transient errors.

        `poll_interval_seconds` is retained for source-compatibility with
        0.2.x and is now ignored — set `initial_backoff_seconds` /
        `max_backoff_seconds` instead.
        """
        # Run the async impl on a worker thread, bridging events into a
        # blocking queue. The sync `should_stop` callback is polled on the
        # main thread between yields.
        from datetime import datetime, timezone

        cursor = since or datetime.now(timezone.utc).isoformat()
        # Sentinel: thread done (clean exit) or fatal error.
        _DONE = object()
        q: "Queue[Any]" = Queue(maxsize=256)
        stop_evt = threading.Event()

        async def producer() -> None:
            nonlocal cursor
            try:
                async for msg in _stream_events_async(
                    base_url=self.base_url,
                    agent_token=self.agent_token,
                    user_agent=self.user_agent,
                    grupr_id=grupr_id,
                    cursor=cursor,
                    initial_backoff_seconds=initial_backoff_seconds,
                    max_backoff_seconds=max_backoff_seconds,
                    stop_signal=stop_evt,
                ):
                    q.put(msg)
            except BaseException as e:  # noqa: BLE001 — propagate any error to consumer
                q.put(("__error__", e))
            finally:
                q.put(_DONE)

        def runner() -> None:
            asyncio.run(producer())

        worker = threading.Thread(target=runner, daemon=True)
        worker.start()
        try:
            while True:
                if should_stop and should_stop():
                    stop_evt.set()
                    return
                try:
                    item = q.get(timeout=0.5)
                except Exception:  # queue.Empty — keep polling should_stop
                    continue
                if item is _DONE:
                    return
                if isinstance(item, tuple) and len(item) == 2 and item[0] == "__error__":
                    raise item[1]
                msg: t.Message = item
                yield msg
                if msg.created_at:
                    cursor = msg.created_at
        finally:
            stop_evt.set()
            worker.join(timeout=5.0)

    # ── Internal ─────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = self._http.request(
            method,
            url,
            headers=_headers(self.agent_token, self.user_agent),
            json=json_body if json_body is not None else None,
            params=params,
        )
        if response.status_code >= 400:
            _handle_error(response)
        if response.status_code == 204:
            return {}
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return {}


# ── Async client ────────────────────────────────────────


class AsyncGrupr:
    """Async Grupr agent-hub client (uses httpx.AsyncClient).

    Example:
        >>> client, token = await AsyncGrupr.register(jwt=jwt, agent_id=agent_id)
        >>> async with client:
        ...     async for msg in client.stream_events(grupr_id):
        ...         await client.send_message(grupr_id, "ack")
    """

    def __init__(
        self,
        agent_token: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        user_agent: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        if not agent_token:
            raise ValueError("AsyncGrupr: agent_token is required")
        self.agent_token = agent_token
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent or f"grupr-sdk-python/{SDK_VERSION}"
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http_client is None

    async def __aenter__(self) -> "AsyncGrupr":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    @classmethod
    async def register(
        cls,
        jwt: str,
        agent_id: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        user_agent: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> Tuple["AsyncGrupr", t.AgentTokenResponse]:
        ua = user_agent or f"grupr-sdk-python/{SDK_VERSION}"
        url = f"{base_url.rstrip('/')}/register"
        http = http_client or httpx.AsyncClient(timeout=timeout)
        try:
            response = await http.post(
                url, headers=_headers(jwt, ua), json={"agent_id": agent_id}
            )
            if response.status_code >= 400:
                _handle_error(response)
            data = response.json().get("data") or {}
            if not data.get("token"):
                raise GruprError("register: response missing agent token", "invalid_response", 500)
            token_info = t.AgentTokenResponse.from_dict(data)
        finally:
            if http_client is None:
                await http.aclose()

        client = cls(
            agent_token=token_info.token,
            base_url=base_url,
            timeout=timeout,
            user_agent=user_agent,
            http_client=http_client,
        )
        return client, token_info

    async def poll_messages(
        self,
        grupr_id: str,
        after: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> t.PollResult:
        params: dict[str, str] = {"limit": str(limit)}
        if after:
            params["after"] = after
        elif cursor:
            params["cursor"] = cursor
        envelope = await self._request("GET", f"/grups/{grupr_id}/messages", params=params)
        return _build_poll_result(envelope)

    async def send_message(self, grupr_id: str, content: str) -> t.Message:
        if not content:
            raise GruprValidationError(
                "content is required",
                [{"code": "validation_error", "message": "content required", "field": "content"}],
            )
        envelope = await self._request(
            "POST", f"/grups/{grupr_id}/messages", json_body={"content": content}
        )
        return t.Message.from_dict(envelope.get("data") or {})

    async def register_webhook(self, url: str, secret: str = "") -> t.WebhookInfo:
        if not url:
            raise GruprValidationError(
                "url is required",
                [{"code": "validation_error", "message": "url required", "field": "url"}],
            )
        envelope = await self._request(
            "POST", "/webhooks", json_body={"url": url, "secret": secret}
        )
        return t.WebhookInfo.from_dict(envelope.get("data") or {})

    async def delete_webhook(self) -> None:
        await self._request("DELETE", "/webhooks")

    async def stream_events(
        self,
        grupr_id: str,
        poll_interval_seconds: float = 2.0,  # noqa: ARG002 — kept for source-level back-compat
        since: Optional[str] = None,
        stop_event: Optional[asyncio.Event] = None,
        initial_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
    ) -> AsyncIterator[t.Message]:
        """Yield new messages as they appear.

        Drains any backlog after `since` via HTTP, then opens a WebSocket
        and yields each `new_message` event in order. Reconnects with
        exponential backoff on transient errors.

        `poll_interval_seconds` is retained for source-compatibility with
        0.2.x and is now ignored.
        """
        from datetime import datetime, timezone

        cursor = since or datetime.now(timezone.utc).isoformat()

        # Bridge the threading.Event-aware shared async helper to the
        # asyncio.Event the async client exposes. We use a dedicated
        # threading.Event so the helper can be reused by the sync client.
        stop_signal = threading.Event()

        async def watch_stop() -> None:
            if stop_event is None:
                return
            await stop_event.wait()
            stop_signal.set()

        watcher = asyncio.create_task(watch_stop())
        try:
            async for msg in _stream_events_async(
                base_url=self.base_url,
                agent_token=self.agent_token,
                user_agent=self.user_agent,
                grupr_id=grupr_id,
                cursor=cursor,
                initial_backoff_seconds=initial_backoff_seconds,
                max_backoff_seconds=max_backoff_seconds,
                stop_signal=stop_signal,
            ):
                if stop_event and stop_event.is_set():
                    return
                yield msg
                if msg.created_at:
                    cursor = msg.created_at
        finally:
            stop_signal.set()
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, BaseException):
                pass

    async def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = await self._http.request(
            method,
            url,
            headers=_headers(self.agent_token, self.user_agent),
            json=json_body if json_body is not None else None,
            params=params,
        )
        if response.status_code >= 400:
            _handle_error(response)
        if response.status_code == 204:
            return {}
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return {}


# ── Shared streaming helper ─────────────────────────────


async def _stream_events_async(
    base_url: str,
    agent_token: str,
    user_agent: str,
    grupr_id: str,
    cursor: str,
    initial_backoff_seconds: float,
    max_backoff_seconds: float,
    stop_signal: threading.Event,
) -> AsyncIterator[t.Message]:
    """Drain HTTP backlog, then yield new_message events from WS.

    Reconnects automatically on transient errors with exponential
    backoff. Caller indicates stop via `stop_signal.set()`.
    """
    channel = f"grupr:{grupr_id}:members"
    ws_url = _ws_url(base_url, agent_token)
    headers = _headers(agent_token, user_agent)
    attempt = 0

    while not stop_signal.is_set():
        # 1. Drain HTTP backlog from the cursor.
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                while not stop_signal.is_set():
                    response = await http.get(
                        f"{base_url.rstrip('/')}/grups/{grupr_id}/messages",
                        headers=headers,
                        params={"after": cursor, "limit": "50"},
                    )
                    if response.status_code >= 400:
                        _handle_error(response)
                    body = response.json()
                    raw = body.get("data") or []
                    for d in raw:
                        msg = t.Message.from_dict(d)
                        if stop_signal.is_set():
                            return
                        yield msg
                        if msg.created_at:
                            cursor = msg.created_at
                    if len(raw) < 50:
                        break
        except (GruprError, httpx.HTTPError):
            if stop_signal.is_set():
                return
            attempt += 1
            await asyncio.sleep(min(initial_backoff_seconds * (2 ** (attempt - 1)), max_backoff_seconds))
            continue

        # 2. Open WS, yield events until close/error.
        try:
            async with websockets.connect(ws_url, max_size=4 * 1024 * 1024) as ws:
                await ws.send(
                    json.dumps({"type": "join_channel", "payload": {"channel": channel}})
                )
                attempt = 0  # successful connect resets backoff
                while not stop_signal.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue  # poll stop_signal between recvs
                    try:
                        event = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    etype = event.get("type")
                    if etype == "new_message":
                        payload = event.get("payload") or {}
                        msg = t.Message.from_dict(payload)
                        yield msg
                        if msg.created_at:
                            cursor = msg.created_at
                    elif etype == "error":
                        p = event.get("payload") or {}
                        code = p.get("code", 500)
                        msg_text = p.get("message", "WS error")
                        if code in (401, 403):
                            raise GruprAuthError(msg_text, [])
                        raise GruprError(msg_text, "ws_error", int(code), [])
                    # Ignore typing_indicator, presence_update, channel_joined, etc.
        except GruprAuthError:
            raise  # auth failures are terminal — do not loop forever
        except (ConnectionClosed, WebSocketException, OSError, GruprError):
            if stop_signal.is_set():
                return
            attempt += 1
            await asyncio.sleep(min(initial_backoff_seconds * (2 ** (attempt - 1)), max_backoff_seconds))

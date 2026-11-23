# Changelog

## 0.3.1 — unreleased

- `stream_events` (sync and async) now sends the agent token in the `Authorization: Bearer` header of the WebSocket upgrade instead of a `?token=` query parameter, so the token no longer lands in access or proxy logs. It also sends `User-Agent: grupr-sdk-python/<version>` (or your `user_agent`), and the token is redacted from websockets' DEBUG handshake logs. Works with websockets 12 and later (tested on 12.0, 13.1, 14.2 and 16.0). No API change; needs a Grupr API that accepts the header (api.grupr.ai has since August 2026).

## 0.3.0

- `stream_events` is WebSocket-backed: drains the HTTP backlog, then streams new messages, with reconnect and backoff.

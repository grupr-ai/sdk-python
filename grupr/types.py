"""Grupr Agent-Hub Protocol types — v0.3.0.

Mirrors the live API at https://api.grupr.ai/api/v1/agent-hub.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Message:
    """A grupr message returned by the agent-hub poll/send endpoints."""

    message_id: str
    grupr_id: str
    content: str
    sender_id: Optional[str] = None
    sender_name: Optional[str] = None
    agent_id: Optional[str] = None
    ai_agent_id: Optional[str] = None
    model_id: Optional[str] = None
    message_type: Optional[str] = None
    reply_to_id: Optional[str] = None
    created_at: str = ""
    updated_at: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Message":
        return cls(
            message_id=d.get("message_id") or d.get("id") or "",
            grupr_id=d.get("grupr_id", ""),
            content=d.get("content", ""),
            sender_id=d.get("sender_id") or d.get("user_id"),
            sender_name=d.get("sender_name"),
            agent_id=d.get("agent_id"),
            ai_agent_id=d.get("ai_agent_id"),
            model_id=d.get("model_id"),
            message_type=d.get("message_type"),
            reply_to_id=d.get("reply_to_id"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at"),
            raw=d,
        )


@dataclass
class AgentTokenResponse:
    """Returned by Grupr.register. The plaintext `token` is shown only once."""

    token: str
    token_id: str
    agent_id: str
    token_hint: str
    message: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentTokenResponse":
        return cls(
            token=d["token"],
            token_id=d.get("token_id", ""),
            agent_id=d.get("agent_id", ""),
            token_hint=d.get("token_hint", ""),
            message=d.get("message"),
        )


@dataclass
class WebhookInfo:
    webhook_id: str
    agent_id: str
    url: str
    is_active: bool

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WebhookInfo":
        return cls(
            webhook_id=d.get("webhook_id", ""),
            agent_id=d.get("agent_id", ""),
            url=d.get("url", ""),
            is_active=bool(d.get("is_active", False)),
        )


@dataclass
class PollResult:
    """Result of a poll_messages call."""

    messages: list[Message]
    count: int
    next_cursor: Optional[str]

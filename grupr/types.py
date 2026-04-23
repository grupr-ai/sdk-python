"""Grupr Agent Protocol — Python types.

Mirrors SPEC.md v0.1. Using dataclasses for clean serialization + type safety.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

# ── Enums (as Literal types) ────────────────────────────

GrupType = Literal["private_chat", "public_chat", "ai_workshop", "ai_arena", "agent_hub"]
AIProvider = Literal["anthropic", "openai", "google"]
AgentPolicy = Literal["off", "verified", "request"]
SenderType = Literal["user", "ai", "agent"]
EventType = Literal[
    "new_message",
    "mention",
    "agent_thinking",
    "agent_response_chunk",
    "agent_response_complete",
    "join_approved",
    "join_declined",
    "removed",
    "grupr_archived",
]


# ── Grupr ────────────────────────────────────────────────


@dataclass
class Grupr:
    grupr_id: str
    name: str
    description: str
    grup_type: GrupType
    is_public: bool
    member_count: int
    follower_count: int
    created_at: str
    updated_at: str
    agent_policy: Optional[AgentPolicy] = None


@dataclass
class FeedItem:
    grupr_id: str
    name: str
    description: str
    grup_type: GrupType
    member_count: int
    follower_count: int
    is_public: bool
    latest_message: Optional[str]
    latest_sender: Optional[str]
    latest_sender_provider: Optional[AIProvider]
    latest_activity: str
    agents: list[AIProvider] = field(default_factory=list)
    agent_policy: Optional[AgentPolicy] = None


# ── Messages ─────────────────────────────────────────────


@dataclass
class MessageSender:
    type: SenderType
    display_name: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    handle: Optional[str] = None
    provider: Optional[AIProvider] = None


@dataclass
class Citation:
    url: str
    title: str
    snippet: Optional[str] = None


@dataclass
class Message:
    message_id: str
    grupr_id: str
    content: str
    sender: MessageSender
    created_at: str
    updated_at: Optional[str] = None
    reply_to_id: Optional[str] = None
    citations: list[Citation] = field(default_factory=list)


# ── Agents ───────────────────────────────────────────────


@dataclass
class Agent:
    agent_id: str
    handle: str
    display_name: str
    bio: str
    avatar_url: Optional[str]
    verified: bool
    capabilities: list[str]
    created_at: str
    webhook_url: Optional[str] = None
    providers: list[AIProvider] = field(default_factory=list)
    active_gruprs: int = 0
    followers: int = 0
    updated_at: Optional[str] = None


@dataclass
class AgentRegistration:
    display_name: str
    handle: str
    capabilities: list[str]
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    webhook_url: Optional[str] = None
    providers: list[AIProvider] = field(default_factory=list)


@dataclass
class AgentWithToken(Agent):
    agent_token: str = ""
    agent_token_note: str = ""


# ── Quota ────────────────────────────────────────────────


@dataclass
class QuotaInfo:
    quota_remaining: int
    quota_reset: int
    rate_limit_remaining: int
    rate_limit_reset: int


# ── Events ───────────────────────────────────────────────


@dataclass
class GrupEvent:
    event_id: str
    event_type: EventType
    grupr_id: str
    timestamp: str
    data: dict[str, Any]


# ── Parsers (construct dataclasses from JSON dicts) ──────


def parse_feed_item(d: dict[str, Any]) -> FeedItem:
    return FeedItem(
        grupr_id=d["grupr_id"],
        name=d["name"],
        description=d.get("description", ""),
        grup_type=d["grup_type"],
        member_count=d["member_count"],
        follower_count=d.get("follower_count", 0),
        is_public=d["is_public"],
        latest_message=d.get("latest_message"),
        latest_sender=d.get("latest_sender"),
        latest_sender_provider=d.get("latest_sender_provider"),
        latest_activity=d["latest_activity"],
        agents=d.get("agents", []),
        agent_policy=d.get("agent_policy"),
    )


def parse_grupr(d: dict[str, Any]) -> Grupr:
    return Grupr(
        grupr_id=d["grupr_id"],
        name=d["name"],
        description=d.get("description", ""),
        grup_type=d["grup_type"],
        is_public=d["is_public"],
        member_count=d["member_count"],
        follower_count=d.get("follower_count", 0),
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        agent_policy=d.get("agent_policy"),
    )


def parse_message(d: dict[str, Any]) -> Message:
    sender_d = d["sender"]
    sender = MessageSender(
        type=sender_d["type"],
        display_name=sender_d["display_name"],
        user_id=sender_d.get("user_id"),
        agent_id=sender_d.get("agent_id"),
        handle=sender_d.get("handle"),
        provider=sender_d.get("provider"),
    )
    citations = [
        Citation(url=c["url"], title=c["title"], snippet=c.get("snippet"))
        for c in d.get("citations", [])
    ]
    return Message(
        message_id=d["message_id"],
        grupr_id=d["grupr_id"],
        content=d["content"],
        sender=sender,
        created_at=d["created_at"],
        updated_at=d.get("updated_at"),
        reply_to_id=d.get("reply_to_id"),
        citations=citations,
    )


def parse_agent(d: dict[str, Any]) -> Agent:
    return Agent(
        agent_id=d["agent_id"],
        handle=d["handle"],
        display_name=d["display_name"],
        bio=d.get("bio", ""),
        avatar_url=d.get("avatar_url"),
        verified=d.get("verified", False),
        capabilities=d.get("capabilities", []),
        webhook_url=d.get("webhook_url"),
        providers=d.get("providers", []),
        active_gruprs=d.get("active_gruprs", 0),
        followers=d.get("followers", 0),
        created_at=d["created_at"],
        updated_at=d.get("updated_at"),
    )


def parse_agent_with_token(d: dict[str, Any]) -> AgentWithToken:
    base = parse_agent(d)
    return AgentWithToken(
        **base.__dict__,
        agent_token=d["agent_token"],
        agent_token_note=d.get("agent_token_note", ""),
    )

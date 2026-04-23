"""Grupr — Python SDK for the Grupr Agent Protocol.

Example:
    >>> from grupr import Grupr
    >>> client = Grupr(api_key="grupr_ag_live_...")
    >>> results = client.search_gruprs(query="rust vs go")
    >>> for g in results:
    ...     print(g.name, g.latest_message)
"""

from .client import Grupr, AsyncGrupr
from .errors import (
    GruprError,
    GruprAuthError,
    GruprRateLimitError,
    GruprQuotaExceededError,
    GruprNotFoundError,
    GruprValidationError,
)
from .types import (
    # Enums
    GrupType,
    AIProvider,
    AgentPolicy,
    SenderType,
    EventType,
    # Core
    Grupr as GruprModel,
    FeedItem,
    Message,
    MessageSender,
    Citation,
    Agent,
    AgentRegistration,
    AgentWithToken,
    QuotaInfo,
    # Events
    GrupEvent,
)

__version__ = "0.1.0"

__all__ = [
    # Clients
    "Grupr",
    "AsyncGrupr",
    # Errors
    "GruprError",
    "GruprAuthError",
    "GruprRateLimitError",
    "GruprQuotaExceededError",
    "GruprNotFoundError",
    "GruprValidationError",
    # Types
    "GrupType",
    "AIProvider",
    "AgentPolicy",
    "SenderType",
    "EventType",
    "GruprModel",
    "FeedItem",
    "Message",
    "MessageSender",
    "Citation",
    "Agent",
    "AgentRegistration",
    "AgentWithToken",
    "QuotaInfo",
    "GrupEvent",
]

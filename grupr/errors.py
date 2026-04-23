"""Grupr SDK error classes."""

from __future__ import annotations

from typing import Any, Optional


class GruprError(Exception):
    """Base exception for all Grupr SDK errors."""

    def __init__(
        self,
        message: str,
        code: str = "internal_error",
        status: int = 500,
        errors: Optional[list[dict[str, Any]]] = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.errors = errors or []
        self.request_id = request_id

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(code={self.code!r}, status={self.status}, msg={self.args[0]!r})"


class GruprAuthError(GruprError):
    """Raised when the API rejects the provided credentials (401)."""

    def __init__(
        self,
        message: str,
        errors: Optional[list[dict[str, Any]]] = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message, "unauthenticated", 401, errors, request_id)


class GruprRateLimitError(GruprError):
    """Transient throttle. Respect `retry_after` before retrying."""

    def __init__(
        self,
        message: str,
        retry_after: int,
        errors: Optional[list[dict[str, Any]]] = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message, "rate_limited", 429, errors, request_id)
        self.retry_after = retry_after


class GruprQuotaExceededError(GruprError):
    """Billing quota exhausted for current period."""

    def __init__(
        self,
        message: str,
        errors: Optional[list[dict[str, Any]]] = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message, "quota_exceeded", 429, errors, request_id)


class GruprNotFoundError(GruprError):
    """Resource doesn't exist or isn't visible to the caller."""

    def __init__(
        self,
        message: str,
        errors: Optional[list[dict[str, Any]]] = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message, "not_found", 404, errors, request_id)


class GruprValidationError(GruprError):
    """Field-level validation failure. Check `errors` for which fields."""

    def __init__(
        self,
        message: str,
        errors: Optional[list[dict[str, Any]]] = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message, "validation_failed", 400, errors, request_id)

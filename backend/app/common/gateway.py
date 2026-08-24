"""Small shared result type for domain services using the Provider Gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class GatewayFetch[T]:
    """Normalized gateway result; provider-specific decisions stay visible."""

    rows: list[T]
    actual_provider: str
    requested_provider: str
    fallback_used: bool = False
    fallback_reason: str | None = None


__all__ = ["GatewayFetch"]

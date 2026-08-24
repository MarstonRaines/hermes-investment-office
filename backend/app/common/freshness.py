"""Freshness contract shared by services, REST and MCP.

Freshness is intentionally separate from ``DataQualityStatus``.  Quality says
how trustworthy an observation is; freshness says whether it is current enough
for today's workflow.  The two values must never be collapsed into one flag.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.common.enums import FreshnessStatus

__all__ = [
    "FreshnessGateError",
    "aggregate_freshness",
    "freshness_payload",
    "require_freshness",
]


class FreshnessGateError(Exception):
    """A decision-sensitive write was attempted with non-OK freshness."""

    code = "FRESHNESS_GATE"

    def __init__(self, message: str = "data freshness is not OK") -> None:
        super().__init__(message)


_RANK = {
    FreshnessStatus.OK: 0,
    FreshnessStatus.WARNING: 1,
    FreshnessStatus.STALE: 2,
    FreshnessStatus.FAILED: 3,
}


def _status(value: object) -> FreshnessStatus:
    if isinstance(value, Mapping):
        value = value.get("status")
    return FreshnessStatus(str(getattr(value, "value", value)))


def aggregate_freshness(domains: Mapping[str, object]) -> str:
    """Return the worst status using FAILED > STALE > WARNING > OK."""

    if not domains:
        return FreshnessStatus.OK.value
    return max((_status(value) for value in domains.values()), key=_RANK.__getitem__).value


def freshness_payload(domains: Mapping[str, object]) -> dict:
    """Build the lossless envelope representation used by TS-04/TS-07."""

    if "overall" in domains and "domains" in domains:
        nested = domains.get("domains")
        return {
            "overall": _status(domains.get("overall")).value,
            "domains": freshness_payload(nested if isinstance(nested, Mapping) else {}).get("domains", {}),
        }
    normalized = {}
    for name, value in domains.items():
        if isinstance(value, Mapping):
            normalized[name] = dict(value)
            normalized[name]["status"] = _status(value).value
        else:
            normalized[name] = {"status": _status(value).value}
    return {"overall": aggregate_freshness(normalized), "domains": normalized}


def require_freshness(value: Mapping[str, object] | str) -> None:
    """Reject every non-OK decision-sensitive write, including WARNING."""

    overall = _status(value if isinstance(value, str) else value.get("overall", value))
    if overall is not FreshnessStatus.OK:
        raise FreshnessGateError(
            f"data_freshness={overall.value}; decision-sensitive write is disabled"
        )

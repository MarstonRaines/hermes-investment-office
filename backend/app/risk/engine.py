"""Pure portfolio risk engine (TS-06 §6).

The engine consumes already materialized market values and NAV history.  It
does not read tables, fetch providers, or write snapshots; callers own those
boundaries.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

ENGINE_VERSION = "risk-engine/0.1.0"


def _ratio(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), ROUND_HALF_UP)


def _level(value: Decimal, warn: Decimal, alert: Decimal | None = None) -> str:
    if alert is not None and value >= alert:
        return "ALERT"
    if value >= warn:
        return "WARN"
    return "OK"


def _max_drawdown(history: list[Decimal]) -> Decimal:
    peak: Decimal | None = None
    maximum = Decimal("0")
    for value in history:
        if value <= 0:
            continue
        peak = value if peak is None else max(peak, value)
        maximum = max(maximum, (peak - value) / peak)
    return _ratio(maximum)


def compute_risk(
    *,
    positions: dict[object, Decimal],
    nav: Decimal,
    nav_history: list[Decimal] | None = None,
    thresholds: dict[str, Decimal] | None = None,
    exposures: dict[str, Decimal] | None = None,
) -> dict:
    """Return concentration, exposure and drawdown with configurable labels.

    ``positions`` contains market values, not quantities.  Values remain
    ``Decimal`` through the calculation so the JSON adapter can serialize the
    exact fixed-point result.
    """

    thresholds = thresholds or {}
    clean_nav = Decimal(nav)
    values = {str(key): Decimal(value) for key, value in positions.items()}
    total = sum(values.values(), Decimal("0"))
    denom = clean_nav if clean_nav > 0 else total
    weights = {
        key: _ratio(value / denom) if denom > 0 else Decimal("0.0000")
        for key, value in values.items()
    }
    max_weight = max(weights.values(), default=Decimal("0.0000"))
    concentration_warn = Decimal(thresholds.get("concentration_warn", Decimal("0.60")))
    concentration_alert = thresholds.get("concentration_alert")
    if concentration_alert is not None:
        concentration_alert = Decimal(concentration_alert)
    exposure_map = {str(key): _ratio(Decimal(value) / denom) if denom > 0 else Decimal("0.0000")
                    for key, value in (exposures or {}).items()}
    drawdown = _max_drawdown([Decimal(value) for value in (nav_history or [])])
    drawdown_warn = Decimal(thresholds.get("drawdown_warn", Decimal("0.10")))
    drawdown_alert = thresholds.get("drawdown_alert")
    if drawdown_alert is not None:
        drawdown_alert = Decimal(drawdown_alert)
    return {
        "engine_version": ENGINE_VERSION,
        "concentration": {
            "weights": weights,
            "max_weight": max_weight,
            "level": _level(max_weight, concentration_warn, concentration_alert),
            "thresholds": {"warn": concentration_warn, "alert": concentration_alert},
        },
        "exposure": {
            "weights": exposure_map,
            "total_invested": _ratio(total / denom) if denom > 0 else Decimal("0.0000"),
        },
        "drawdown": {
            "max_drawdown": drawdown,
            "level": _level(drawdown, drawdown_warn, drawdown_alert),
            "thresholds": {"warn": drawdown_warn, "alert": drawdown_alert},
        },
    }


__all__ = ["ENGINE_VERSION", "compute_risk"]

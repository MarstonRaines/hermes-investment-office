"""确定性组合风险引擎（TS-06 §6）。

九项指标始终返回固定结构。输入不足时返回 ``None`` 与质量标记，不会用猜测值
填充，也不会中断每日流水线。读取事实、穿透 ETF 与持久化均由服务层负责。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from math import sqrt
from typing import Any

ENGINE_VERSION = "risk-engine/0.2.0"


def _ratio(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), ROUND_HALF_UP)


def _level(value: Decimal, warn: Decimal | None, alert: Decimal | None = None) -> str:
    if alert is not None and value >= alert:
        return "ALERT"
    if warn is not None and value >= warn:
        return "WARN"
    return "OK"


def _threshold(thresholds: dict[str, Any], *names: str) -> Decimal | None:
    for name in names:
        if thresholds.get(name) is not None:
            return Decimal(str(thresholds[name]))
    return None


def _drawdown(
    history: list[Decimal], dates: list[date] | None = None,
) -> tuple[Decimal, str | None, str | None]:
    peak: Decimal | None = None
    peak_index: int | None = None
    maximum = Decimal("0")
    maximum_peak: int | None = None
    trough_index: int | None = None
    for index, value in enumerate(history):
        if value <= 0:
            continue
        if peak is None or value > peak:
            peak = value
            peak_index = index
        current = (peak - value) / peak
        if current > maximum:
            maximum = current
            maximum_peak = peak_index
            trough_index = index
    peak_date = dates[maximum_peak].isoformat() if dates and maximum_peak is not None else None
    trough_date = dates[trough_index].isoformat() if dates and trough_index is not None else None
    return _ratio(maximum), peak_date, trough_date


def _returns(values: list[Decimal]) -> list[Decimal]:
    result: list[Decimal] = []
    for previous, current in zip(values, values[1:], strict=False):
        if previous > 0:
            result.append((current / previous) - Decimal("1"))
    return result


def _correlation(left: list[Decimal], right: list[Decimal]) -> Decimal | None:
    count = min(len(left), len(right))
    if count < 2:
        return None
    x = [float(value) for value in left[-count:]]
    y = [float(value) for value in right[-count:]]
    mean_x = sum(x) / count
    mean_y = sum(y) / count
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True))
    denominator = sqrt(
        sum((a - mean_x) ** 2 for a in x) * sum((b - mean_y) ** 2 for b in y)
    )
    if denominator == 0:
        return None
    return _ratio(Decimal(str(numerator / denominator)))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _input_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        _json_safe(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_risk(
    *,
    positions: dict[object, Decimal],
    nav: Decimal,
    nav_history: list[Decimal] | None = None,
    thresholds: dict[str, Any] | None = None,
    exposures: dict[str, Decimal] | None = None,
    as_of: date | datetime | None = None,
    snapshot_dates: list[date] | None = None,
    cash_cny: Decimal = Decimal("0"),
    asset_classes: dict[object, str] | None = None,
    sector_exposures: dict[str, Decimal] | None = None,
    etf_overlap: list[dict[str, Any]] | None = None,
    position_history: dict[object, list[Decimal]] | None = None,
    price_history: dict[object, list[Decimal]] | None = None,
    valuation_bands: dict[object, str] | None = None,
    passthrough: list[dict[str, Any]] | None = None,
    provenance_id: str | None = None,
    window_days: int = 252,
    min_obs: int = 120,
) -> dict:
    """计算 TS-06 冻结的九项风险指标，并保留旧版兼容视图。"""

    thresholds = thresholds or {}
    clean_nav = Decimal(nav)
    original_keys = {str(key): key for key in positions}
    values = {str(key): Decimal(value or 0) for key, value in positions.items()}
    total = sum(values.values(), Decimal("0"))
    denominator = clean_nav if clean_nav > 0 else total
    weights = {
        key: _ratio(value / denominator) if denominator > 0 else Decimal("0.0000")
        for key, value in values.items()
    }
    max_instrument = max(weights, key=weights.get) if weights else None
    max_weight = weights.get(max_instrument, Decimal("0.0000"))
    concentration_warn = _threshold(
        thresholds, "position_concentration_warn", "concentration_warn"
    )
    concentration_alert = _threshold(
        thresholds, "position_concentration_alert", "concentration_alert"
    )

    flags: list[str] = []
    asset_values: dict[str, Decimal] = {}
    if asset_classes:
        for instrument_id, value in values.items():
            original = original_keys[instrument_id]
            asset_class = asset_classes.get(original) or asset_classes.get(instrument_id)
            label = asset_class or "UNCLASSIFIED"
            asset_values[label] = asset_values.get(label, Decimal("0")) + value
    elif exposures:
        asset_values = {str(key): Decimal(value) for key, value in exposures.items()}
    elif values:
        flags.append("ASSET_CLASS_MAPPING_MISSING")
    if cash_cny != 0 or denominator > 0:
        asset_values["CASH"] = Decimal(cash_cny)
    asset_weights = {
        key: _ratio(value / denominator) if denominator > 0 else Decimal("0.0000")
        for key, value in asset_values.items()
    }

    sectors = {str(key): Decimal(value) for key, value in (sector_exposures or {}).items()}
    sector_weights = {
        key: _ratio(value / denominator) if denominator > 0 else Decimal("0.0000")
        for key, value in sectors.items()
    }
    max_sector = max(sector_weights, key=sector_weights.get) if sector_weights else None
    max_sector_weight = sector_weights.get(max_sector) if max_sector else None
    if values and not sectors:
        flags.append("SECTOR_MAPPING_MISSING")

    nav_values = [Decimal(value) for value in (nav_history or [])]
    maximum_drawdown, peak_date, trough_date = _drawdown(nav_values, snapshot_dates)
    if len(nav_values) < 2:
        flags.append("INSUFFICIENT_NAV_HISTORY")

    position_drawdowns = []
    for instrument_id, history in (position_history or {}).items():
        value, _, _ = _drawdown([Decimal(item) for item in history])
        position_drawdowns.append({"instrument_id": str(instrument_id), "max_drawdown": value})
    if values and not position_drawdowns:
        flags.append("POSITION_HISTORY_MISSING")

    correlation_pairs = []
    price_returns = {
        str(key): _returns([Decimal(item) for item in history])
        for key, history in (price_history or {}).items()
    }
    keys = sorted(price_returns)
    for index, left_key in enumerate(keys):
        for right_key in keys[index + 1:]:
            left = price_returns[left_key]
            right = price_returns[right_key]
            observations = min(len(left), len(right))
            value = _correlation(left, right) if observations >= min_obs else None
            correlation_pairs.append({
                "pair": [left_key, right_key],
                "corr": value,
                "observations": observations,
            })
    if len(values) > 1 and not any(item["corr"] is not None for item in correlation_pairs):
        flags.append("INSUFFICIENT_CORRELATION_HISTORY")

    expensive = {"EXPENSIVE", "VERY_EXPENSIVE"}
    bands = {str(key): value for key, value in (valuation_bands or {}).items()}
    expensive_value = sum(
        (value for key, value in values.items() if bands.get(key) in expensive),
        Decimal("0"),
    )
    valuation_concentration = (
        _ratio(expensive_value / denominator) if denominator > 0 and bands else None
    )
    if values and not bands:
        flags.append("VALUATION_BAND_MISSING")

    overlap = etf_overlap or []
    if len(values) > 1 and not overlap:
        flags.append("ETF_HOLDINGS_MISSING")

    drawdown_warn = _threshold(thresholds, "portfolio_drawdown_warn", "drawdown_warn")
    drawdown_alert = _threshold(thresholds, "portfolio_drawdown_alert", "drawdown_alert")
    sector_warn = _threshold(thresholds, "sector_concentration_warn")
    cash_ratio = _ratio(Decimal(cash_cny) / denominator) if denominator > 0 else Decimal("0.0000")
    input_payload = {
        "positions": values,
        "nav": clean_nav,
        "nav_history": nav_values,
        "cash_cny": cash_cny,
        "asset_classes": asset_classes or {},
        "sector_exposures": sectors,
        "etf_overlap": overlap,
        "position_history": position_history or {},
        "price_history": price_history or {},
        "valuation_bands": bands,
        "thresholds": thresholds,
        "window_days": window_days,
        "min_obs": min_obs,
    }
    metrics = {
        "position_concentration": {
            "weights": weights,
            "max_weight": max_weight,
            "instrument_id": max_instrument,
            "warn_threshold": concentration_warn,
            "level": _level(max_weight, concentration_warn, concentration_alert),
        },
        "sector_concentration": {
            "weights": sector_weights,
            "max_sector_weight": max_sector_weight,
            "sector": max_sector,
            "level": (
                _level(max_sector_weight, sector_warn)
                if max_sector_weight is not None else "UNAVAILABLE"
            ),
        },
        "asset_class_exposure": asset_weights,
        "etf_overlap": overlap,
        "portfolio_drawdown": {
            "max_drawdown": maximum_drawdown,
            "peak_date": peak_date,
            "trough_date": trough_date,
            "window_days": window_days,
            "level": _level(maximum_drawdown, drawdown_warn, drawdown_alert),
        },
        "position_drawdown": position_drawdowns,
        "correlation": {
            "pairs": correlation_pairs,
            "window_days": window_days,
            "min_obs": min_obs,
        },
        "cash_ratio": cash_ratio,
        "valuation_concentration": valuation_concentration,
    }
    return {
        "engine_version": ENGINE_VERSION,
        "as_of": as_of.isoformat() if as_of else None,
        "input_hash": _input_hash(input_payload),
        "provenance_id": provenance_id,
        "metrics": metrics,
        "passthrough": passthrough or [],
        "quality_flags": sorted(set(flags)),
        "window": {"window_days": window_days, "min_obs": min_obs},
        # 兼容 0.1 读取方；新代码应读取 metrics。
        "concentration": {
            "weights": weights,
            "max_weight": max_weight,
            "level": metrics["position_concentration"]["level"],
            "thresholds": {"warn": concentration_warn, "alert": concentration_alert},
        },
        "exposure": {
            "weights": asset_weights,
            "total_invested": _ratio(total / denominator) if denominator > 0 else Decimal("0.0000"),
        },
        "drawdown": {
            "max_drawdown": maximum_drawdown,
            "level": metrics["portfolio_drawdown"]["level"],
            "thresholds": {"warn": drawdown_warn, "alert": drawdown_alert},
        },
    }


__all__ = ["ENGINE_VERSION", "compute_risk"]

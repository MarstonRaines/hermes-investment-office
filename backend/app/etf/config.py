"""Configuration for ETF calculations.

Valuation bands and QDII date-alignment limits are runtime configuration.  The
engine receives the parsed values; it does not own policy constants.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class ValuationBandConfig(BaseModel):
    version: str
    method: str = "PE_PERCENTILE"
    thresholds: dict[str, Decimal]
    composite_weights: dict[str, Decimal] = Field(default_factory=dict)
    min_history: int = 20

    @model_validator(mode="after")
    def validate_thresholds(self) -> ValuationBandConfig:
        keys = ("very_cheap_lt", "cheap_lt", "fair_lt", "expensive_lt")
        values = [self.thresholds.get(key) for key in keys]
        if any(value is None for value in values):
            raise ValueError(f"估值带阈值缺少字段: {keys}")
        if any(not Decimal("0") <= value <= Decimal("1") for value in values):
            raise ValueError("估值带阈值必须位于 [0, 1]")
        if values != sorted(values):
            raise ValueError("估值带阈值必须单调递增")
        if self.method not in {"PE_PERCENTILE", "COMPOSITE_PE_PB"}:
            raise ValueError(f"不支持的估值带方法: {self.method}")
        if self.min_history < 1:
            raise ValueError("min_history 必须 >= 1")
        return self

    @property
    def config_hash(self) -> str:
        payload = self.model_dump(mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def load_valuation_band_config(path: str | Path) -> ValuationBandConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"etf valuation band config 不存在: {p}")
    return ValuationBandConfig.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


class FreshnessDomainConfig(BaseModel):
    """Session lag thresholds for one ETF freshness domain."""

    warn_lag_sessions: int = Field(ge=0)
    stale_lag_sessions: int = Field(ge=0)


class FreshnessThresholdConfig(BaseModel):
    market: FreshnessDomainConfig = Field(
        default_factory=lambda: FreshnessDomainConfig(
            warn_lag_sessions=1, stale_lag_sessions=2
        )
    )
    etf_nav: FreshnessDomainConfig = Field(
        default_factory=lambda: FreshnessDomainConfig(
            warn_lag_sessions=1, stale_lag_sessions=2
        )
    )
    etf_holdings: FreshnessDomainConfig = Field(
        default_factory=lambda: FreshnessDomainConfig(
            warn_lag_sessions=60, stale_lag_sessions=120
        )
    )
    index: FreshnessDomainConfig = Field(
        default_factory=lambda: FreshnessDomainConfig(
            warn_lag_sessions=1, stale_lag_sessions=2
        )
    )
    fx: FreshnessDomainConfig = Field(
        default_factory=lambda: FreshnessDomainConfig(
            warn_lag_sessions=1, stale_lag_sessions=2
        )
    )


class QDIIAlignmentConfig(BaseModel):
    """Maximum trading-session distance for each QDII relationship."""

    version: str
    max_market_nav_days: int = Field(ge=0)
    max_underlying_market_days: int = Field(ge=0)
    max_fx_underlying_days: int = Field(ge=0)
    max_nav_underlying_days: int = Field(ge=0)
    freshness: FreshnessThresholdConfig = Field(default_factory=FreshnessThresholdConfig)

    @property
    def config_hash(self) -> str:
        payload = self.model_dump(mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def load_qdii_alignment_config(path: str | Path) -> QDIIAlignmentConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"QDII 对齐配置不存在: {p}")
    return QDIIAlignmentConfig.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


__all__ = [
    "ValuationBandConfig",
    "FreshnessDomainConfig",
    "FreshnessThresholdConfig",
    "QDIIAlignmentConfig",
    "load_valuation_band_config",
    "load_qdii_alignment_config",
]

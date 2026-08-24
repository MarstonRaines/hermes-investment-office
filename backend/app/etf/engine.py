"""Deterministic ETF Engine (M3).

This module contains no Provider or database access. The service layer supplies
normalized facts fetched through the Provider Gateway and persists the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.common.enums import DataQualityStatus, QuotaStatus
from app.etf.config import QDIIAlignmentConfig, ValuationBandConfig

ENGINE_VERSION = "etf-engine/0.1.0"
@dataclass(frozen=True)
class ETFMetricInput:
    instrument_id: UUID
    as_of: datetime
    market_date: date
    market_price_cny: Decimal | None
    is_qdii: bool
    underlying_index_id: UUID | None = None
    nav: Decimal | None = None
    nav_date: date | None = None
    reference_nav_basis: str | None = None
    underlying_session_date: date | None = None
    index_close: Decimal | None = None
    index_previous_close: Decimal | None = None
    fx_rate: Decimal | None = None
    fx_previous_rate: Decimal | None = None
    fx_as_of: datetime | None = None
    market_nav_distance: int | None = None
    underlying_market_distance: int | None = None
    fx_underlying_distance: int | None = None
    nav_underlying_distance: int | None = None
    index_pe: Decimal | None = None
    index_pb: Decimal | None = None
    pe_percentile: Decimal | None = None
    pb_percentile: Decimal | None = None
    quota_status: QuotaStatus | None = None
    net_value_t1: Decimal | None = None


@dataclass
class ETFMetricOutput:
    premium_discount: Decimal | None = None
    fx_contribution: Decimal | None = None
    r_usd: Decimal | None = None
    fx_chg: Decimal | None = None
    r_cny: Decimal | None = None
    reference_nav_basis: str | None = None
    valuation_band: str | None = None
    band_basis: str | None = None
    band_inputs: dict[str, str] = field(default_factory=dict)
    band_thresholds_hash: str | None = None
    quality_flags: list[str] = field(default_factory=list)
    quality_status: DataQualityStatus = DataQualityStatus.VERIFIED
    quota_status: QuotaStatus = QuotaStatus.NOT_APPLICABLE

    @property
    def details(self) -> dict[str, Any]:
        return {
            "r_usd": _str_or_none(self.r_usd),
            "fx_chg": _str_or_none(self.fx_chg),
            "r_cny": _str_or_none(self.r_cny),
        }


class ETFEngine:
    """ETF-only arithmetic; individual-stock DCF never enters this class."""

    def __init__(
        self,
        *,
        band_config: ValuationBandConfig,
        alignment_config: QDIIAlignmentConfig | None = None,
    ) -> None:
        self.band_config = band_config
        self.alignment_config = alignment_config or QDIIAlignmentConfig(
            version="test-default",
            max_market_nav_days=1,
            max_underlying_market_days=1,
            max_fx_underlying_days=1,
            max_nav_underlying_days=1,
        )

    def compute(self, inputs: ETFMetricInput) -> ETFMetricOutput:
        output = ETFMetricOutput(
            quota_status=(
                inputs.quota_status
                if inputs.is_qdii and inputs.quota_status is not None
                else (QuotaStatus.UNKNOWN if inputs.is_qdii else QuotaStatus.NOT_APPLICABLE)
            )
        )
        self._premium(inputs, output)
        if inputs.is_qdii:
            self._qdii_fx(inputs, output)
            if output.quota_status is None:
                output.quota_status = QuotaStatus.UNKNOWN
        self._valuation_band(inputs, output)
        output.quality_status = (
            # The frozen DataQualityStatus enum has no WARNING member;
            # warning semantics are carried by explicit quality_flags.
            DataQualityStatus.ACCEPTABLE
            if output.quality_flags
            else DataQualityStatus.VERIFIED
        )
        return output

    def _premium(self, inputs: ETFMetricInput, output: ETFMetricOutput) -> None:
        if inputs.market_price_cny is None:
            output.quality_flags.append("MARKET_PRICE_MISSING")
            return
        if inputs.nav is None or inputs.nav_date is None:
            if inputs.is_qdii:
                output.quality_flags.append("NAV_MISSING")
            return
        if inputs.reference_nav_basis is None:
            output.quality_flags.append("REFERENCE_NAV_BASIS_MISSING")
            return
        output.reference_nav_basis = inputs.reference_nav_basis
        if inputs.nav <= 0:
            output.quality_flags.append("NAV_INVALID")
            return
        distance = inputs.market_nav_distance
        if distance is None or distance > self.alignment_config.max_market_nav_days:
            output.quality_flags.append("NAV_TIME_ALIGNMENT_FAILED")
            return
        output.premium_discount = (
            inputs.market_price_cny - inputs.nav
        ) / inputs.nav

    def _qdii_fx(self, inputs: ETFMetricInput, output: ETFMetricOutput) -> None:
        if inputs.underlying_index_id is None or inputs.underlying_session_date is None:
            output.quality_flags.append("UNDERLYING_INDEX_MISSING")
            return
        if (
            inputs.underlying_market_distance is None
            or inputs.underlying_market_distance > self.alignment_config.max_underlying_market_days
        ):
            output.quality_flags.append("UNDERLYING_TIME_ALIGNMENT_FAILED")
            return
        if (
            inputs.nav_date is None
            or inputs.nav_underlying_distance is None
            or inputs.nav_underlying_distance > self.alignment_config.max_nav_underlying_days
        ):
            output.quality_flags.append("NAV_TIME_ALIGNMENT_FAILED")
            output.premium_discount = None
        if inputs.index_close is None or inputs.index_previous_close is None:
            output.quality_flags.append("INDEX_HISTORY_MISSING")
            return
        if inputs.index_previous_close == 0:
            output.quality_flags.append("INDEX_HISTORY_INVALID")
            return
        if inputs.fx_rate is None or inputs.fx_previous_rate is None:
            output.quality_flags.append("FX_MISSING")
            return
        if inputs.fx_previous_rate == 0:
            output.quality_flags.append("FX_INVALID")
            return
        if inputs.fx_as_of is None:
            output.quality_flags.append("FX_MISSING")
            return
        if (
            inputs.fx_underlying_distance is None
            or inputs.fx_underlying_distance > self.alignment_config.max_fx_underlying_days
        ):
            output.quality_flags.append("FX_TIME_ALIGNMENT_FAILED")
            return
        output.r_usd = inputs.index_close / inputs.index_previous_close - Decimal("1")
        output.fx_chg = inputs.fx_rate / inputs.fx_previous_rate - Decimal("1")
        output.r_cny = (Decimal("1") + output.r_usd) * (
            Decimal("1") + output.fx_chg
        ) - Decimal("1")
        output.fx_contribution = output.r_cny - output.r_usd

    def _valuation_band(
        self, inputs: ETFMetricInput, output: ETFMetricOutput
    ) -> None:
        values: dict[str, Decimal] = {}
        if inputs.pe_percentile is not None:
            values["pe_percentile"] = inputs.pe_percentile
        if inputs.pb_percentile is not None:
            values["pb_percentile"] = inputs.pb_percentile
        if self.band_config.method == "PE_PERCENTILE":
            percentile = values.get("pe_percentile")
            basis = "PE_PERCENTILE"
        else:
            if "pe_percentile" not in values or "pb_percentile" not in values:
                output.quality_flags.append("INDEX_VALUATION_UNAVAILABLE")
                return
            weights = self.band_config.composite_weights
            try:
                percentile = (
                    values["pe_percentile"] * weights["pe"]
                    + values["pb_percentile"] * weights["pb"]
                )
            except (KeyError, InvalidOperation):
                output.quality_flags.append("INDEX_VALUATION_UNAVAILABLE")
                return
            basis = "COMPOSITE_PE_PB"
        if percentile is None or not Decimal("0") <= percentile <= Decimal("1"):
            output.quality_flags.append("INDEX_VALUATION_UNAVAILABLE")
            return
        output.valuation_band = _band_for_percentile(percentile, self.band_config)
        output.band_basis = basis
        output.band_inputs = {key: str(value) for key, value in values.items()}
        output.band_thresholds_hash = self.band_config.config_hash


def _band_for_percentile(value: Decimal, config: ValuationBandConfig) -> str:
    thresholds = config.thresholds
    if value < thresholds["very_cheap_lt"]:
        return "VERY_CHEAP"
    if value < thresholds["cheap_lt"]:
        return "CHEAP"
    if value < thresholds["fair_lt"]:
        return "FAIR"
    if value < thresholds["expensive_lt"]:
        return "EXPENSIVE"
    return "VERY_EXPENSIVE"


def _str_or_none(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


__all__ = [
    "ENGINE_VERSION", "ETFMetricInput", "ETFMetricOutput", "ETFEngine",
]

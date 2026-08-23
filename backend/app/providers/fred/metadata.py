# =====================================================================
# backend/app/providers/fred/metadata.py
# =====================================================================
from app.providers.contracts.base import QualityTier

__all__ = ["FredMeta"]


class FredMeta:
    provider_name = "fred"
    display_name = "FRED（St. Louis Fed）"
    quality_tier = QualityTier.TIER_1
    quality_score = 0.98
    known_limits = [
        "需 API key（HERMES_FRED_API_KEY，2026-08-23 已配置）",
        "发布有延迟（DEXCHUS 2026-08-14 数据于 08-23 实测 6.7412）",
        "DEXCHUS 与 Yahoo USDCNY=X 口径/时点差异 ~0.4%，作权威交叉验证（S7）",
    ]

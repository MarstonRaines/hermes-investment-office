# =====================================================================
# backend/app/providers/legulegu/metadata.py
# =====================================================================
from app.providers.contracts.base import QualityTier

__all__ = ["LeguleguMeta"]


class LeguleguMeta:
    provider_name = "legulegu"
    display_name = "乐咕乐股（legulegu）"
    quality_tier = QualityTier.TIER_2
    quality_score = 0.92
    known_limits = [
        "S6 实测首选：沪深300 PE 5194 行 / 中证500 4765 行（20+ 年历史）",
        "仅覆盖 A 股主要宽基；美股指数估值源 spike 后 PENDING",
        "数据延迟发布，freshness 契约需容忍",
        "PE/PB 取 TTM 口径（ttmPe/ttmPb，缺失时退 static）",
    ]

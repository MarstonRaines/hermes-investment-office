# =====================================================================
# backend/app/providers/yahoo/metadata.py
# =====================================================================
from app.providers.contracts.base import QualityTier

__all__ = ["YahooMeta"]


class YahooMeta:
    provider_name = "yahoo"
    display_name = "Yahoo Finance"
    quality_tier = QualityTier.TIER_3
    quality_score = 0.80
    known_limits = [
        "非官方接口，可能被限/被封（S3 实测依赖代理环境）",
        "仅 INDEX 标的（^GSPC/^NDX），不抓 SPY/VOO/QQQ（冻结规范 §12.3）",
        "美股指数估值源（Shiller PE）spike 后 PENDING，v0.1 不实现",
    ]

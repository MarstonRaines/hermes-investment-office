# =====================================================================
# backend/app/providers/akshare/metadata.py —— AkShare 分源元数据（ADR-005 D3）
# =====================================================================
from app.providers.contracts.base import QualityTier

__all__ = ["AKSHARE_QUALITY_SCORE"]


class AkShareMeta:
    provider_name = "akshare"
    display_name = "AkShare"
    quality_tier = QualityTier.TIER_3
    quality_score = 0.85
    known_limits = [
        "开源爬虫库，字段稳定性差、无 SLA（S2 实测）",
        "eastmoney 源直连被阻，必须走显式代理（ADR-005）",
        "A 股日线 fallback 用新浪源 stock_zh_a_daily（ADR-005 D3）",
        "同花顺财务摘要作财务 fallback（S2 实测）",
    ]

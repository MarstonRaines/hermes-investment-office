# =====================================================================
# backend/app/providers/tushare/metadata.py —— TuShare 元数据声明（TS-05 §3.3）
# =====================================================================
from app.providers.contracts.base import QualityTier


class TuShareMeta:
    provider_name = "tushare"
    display_name = "TuShare Pro"
    quality_tier = QualityTier.TIER_2
    quality_score = 0.96
    known_limits = [
        "积分制：2000 积分档实测全通（S1，2026-08-23）",
        "ETF 行情必须走 fund_daily，daily 对 ETF 返回空（S1 关键发现）",
        "财务报表数值单位恒为元（S5）；index_weight 数据延迟发布",
        "接口字段与积分档位可能调整（TuShare 公告为准）",
    ]

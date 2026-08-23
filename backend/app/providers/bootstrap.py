# =====================================================================
# backend/app/providers/bootstrap.py —— 全部 Provider 实现汇总注册
#
# 架构测试 A4（TS-05 §4.3 / ts08）：provider-capability.yaml ↔ 注册表 ↔ 实现
# 三方一致。本模块是"实现 → 注册表"的唯一入口（应用启动与测试共用）。
#
# 每个上游一个唯一实现类（TS-05 §3.1：provider_name 唯一），多接口合并实现：
#   tushare           —— 行情/财务/NAV/指数（6 能力）
#   akshare_sina      —— A 股日线 fallback（ADR-005 D3）
#   akshare_eastmoney —— ETF 行情/NAV/持仓/quota（显式代理，ADR-005）
#   akshare_ths       —— 财务摘要 fallback（S2）
#   yahoo / fred / legulegu —— 宏观/FX/指数估值
# =====================================================================
from __future__ import annotations

from app.providers.akshare import (
    AkShareEastmoneyEtfProvider,
    AkShareSinaMarketProvider,
    AkShareThsFundamentalProvider,
)
from app.providers.contracts.base import BaseProvider
from app.providers.fred import FredMacroProvider
from app.providers.legulegu import LeguleguIndexValuationProvider
from app.providers.registry import ProviderRegistry
from app.providers.tushare import TuShareProvider
from app.providers.yahoo import YahooMacroProvider

__all__ = ["ALL_PROVIDERS", "register_all_providers"]

ALL_PROVIDERS: list[type[BaseProvider]] = [
    TuShareProvider,                    # CN_DAILY_QUOTE / CN_ETF_QUOTE / ADJ_FACTOR / FINANCIAL_STATEMENTS / FUND_NAV / INDEX_QUOTE(aux)
    AkShareSinaMarketProvider,          # CN_DAILY_QUOTE / CN_ETF_QUOTE / ADJ_FACTOR（fallback）
    AkShareEastmoneyEtfProvider,        # CN_ETF_QUOTE / FUND_NAV / FUND_HOLDINGS / QUOTA_STATUS
    AkShareThsFundamentalProvider,      # FINANCIAL_STATEMENTS（fallback）
    YahooMacroProvider,                 # INDEX_QUOTE / FX_RATES
    FredMacroProvider,                  # MACRO_SERIES / FX_RATES（交叉验证）
    LeguleguIndexValuationProvider,     # INDEX_VALUATION
]


def register_all_providers(registry: ProviderRegistry) -> None:
    """把全部实现注册进 registry（重复注册抛 ProviderConfigError，幂等由调用方保证）。"""
    registry.register_all(ALL_PROVIDERS)

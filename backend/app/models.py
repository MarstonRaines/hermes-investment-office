# backend/app/models.py —— 聚合全部 ORM 模型
# 用途：仅 Alembic env.py 与架构测试需要完整 metadata。
# 业务代码禁止从这里跨模块 import（§6.1 白名单纪律；跨模块引用走所属模块的 __init__.py）。
from app.audit.models import AuditEvent, OutboxEvent, ProvenanceRecord
from app.briefing.models import AttentionItem, DailyBrief, DailyContext
from app.calendar.models import TradingCalendarEntry
from app.corporate_actions.models import CorporateAction
from app.etf.models import (
    ETFHoldingSnapshot,
    ETFMetricSnapshot,
    ETFNavObservation,
    ETFProfile,
)
from app.fundamentals.models import FinancialFact
from app.fx.models import FXObservation
from app.instruments.models import Instrument, ProviderSymbol, Watchlist, WatchlistMember
from app.jobs.models import JobRun
from app.market_data.models import IndexBarIndex, MarketBarIndex
from app.portfolio.models import (
    Account,
    Portfolio,
    PortfolioSnapshot,
    PortfolioTransaction,
    PositionSnapshot,
    TargetAllocation,
    TradeProposal,
)
from app.research.models import (
    EvidenceItem,
    EvidenceLink,
    ResearchEvent,
    ResearchNote,
    ResearchThread,
    ResearchWorkspace,
)
from app.thesis.models import (
    Thesis,
    ThesisAssumption,
    ThesisEvent,
    ThesisRedFlag,
    ThesisReview,
    ThesisRevision,
)
from app.valuation.models import ValuationAssumption, ValuationInputRef, ValuationRun

__all__ = [  # 43 个模型类
    "Instrument", "ProviderSymbol", "Watchlist", "WatchlistMember",
    "ETFProfile", "ETFNavObservation", "ETFHoldingSnapshot",
    "ETFMetricSnapshot",
    "MarketBarIndex", "IndexBarIndex", "FinancialFact", "FXObservation",
    "ProvenanceRecord", "AuditEvent", "OutboxEvent", "JobRun",
    "Thesis", "ThesisRevision", "ThesisAssumption", "ThesisReview",
    "ThesisRedFlag", "ThesisEvent", "EvidenceItem", "EvidenceLink",
    "ValuationRun", "ValuationAssumption", "ValuationInputRef",
    "Portfolio", "Account", "PortfolioTransaction", "PositionSnapshot",
    "PortfolioSnapshot", "TargetAllocation", "TradeProposal",
    "ResearchWorkspace", "ResearchThread", "ResearchEvent", "ResearchNote",
    "DailyContext", "AttentionItem", "DailyBrief",
    "TradingCalendarEntry", "CorporateAction",
]

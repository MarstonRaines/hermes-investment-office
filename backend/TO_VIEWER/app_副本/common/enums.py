# backend/app/common/enums.py
# =====================================================================
# Hermes Investment Office —— 全库枚举（唯一事实来源）
# 一致性约束：所有值必须与 ts01 §关键枚举、ts02 各表 CHECK 完全一致。
# 约定：StrEnum；DB 存 .value（TEXT + CHECK）；JSON 输出 .value。
#       禁止 enum.auto()；AT_RISK 不是合法值（冻结规范 §27.1）。
# =====================================================================
from enum import StrEnum


class InstrumentType(StrEnum):
    """资产类型：v0.1 冻结三类型；QDII 是 CN_ETF 特征，绝不新增 US_ETF（ts01/ts02 冻结）。"""
    CN_EQUITY = "CN_EQUITY"
    CN_ETF = "CN_ETF"
    INDEX = "INDEX"


class InstrumentStatus(StrEnum):
    """上市状态（独立于 Thesis 生命周期，ts02 §3.1）。"""
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELISTED = "DELISTED"


class InstrumentMarket(StrEnum):
    """instruments.market（ts02 §3.1 文档值 SSE/SZSE；DB 层 TEXT NOT NULL，边界层校验）。"""
    SSE = "SSE"
    SZSE = "SZSE"


class SourceKind(StrEnum):
    """provenance_records.source_kind（ts01 provenance 契约）。"""
    OFFICIAL_FILING = "OFFICIAL_FILING"
    EXCHANGE = "EXCHANGE"
    PROVIDER = "PROVIDER"
    HUMAN = "HUMAN"
    HERMES = "HERMES"
    DERIVED_ENGINE = "DERIVED_ENGINE"


class DataQualityStatus(StrEnum):
    """provenance_records / 各 facts 的 quality_status（ts01/ts02 冻结）。"""
    VERIFIED = "VERIFIED"
    ACCEPTABLE = "ACCEPTABLE"
    STALE = "STALE"
    CONFLICT = "CONFLICT"
    REJECTED = "REJECTED"


class ThesisLifecycleStatus(StrEnum):
    """Thesis 生命周期（与 health 正交，ts01 §4）。"""
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    UNDER_REVIEW = "UNDER_REVIEW"
    INVALIDATED = "INVALIDATED"
    ARCHIVED = "ARCHIVED"


class ThesisHealthStatus(StrEnum):
    """Thesis / 假设健康度（冻结规范 §27.1）。注意：AT_RISK 不是合法值。"""
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    BROKEN = "BROKEN"


class Conviction(StrEnum):
    HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"


class AssumptionCategory(StrEnum):
    BUSINESS, FINANCIAL, VALUATION, MACRO, GOVERNANCE = (
        "BUSINESS", "FINANCIAL", "VALUATION", "MACRO", "GOVERNANCE")


class ReviewType(StrEnum):
    SCHEDULED_QUARTERLY, EVENT_DRIVEN, RED_FLAG, DRIFT_CHECK = (
        "SCHEDULED_QUARTERLY", "EVENT_DRIVEN", "RED_FLAG", "DRIFT_CHECK")


class ReviewConclusion(StrEnum):
    REAFFIRM, REVISE, INVALIDATE = "REAFFIRM", "REVISE", "INVALIDATE"


class RedFlagSeverity(StrEnum):
    RED_LINE, HIGH, MEDIUM = "RED_LINE", "HIGH", "MEDIUM"


class RedFlagStatus(StrEnum):
    ARMED, TRIGGERED, RESOLVED = "ARMED", "TRIGGERED", "RESOLVED"


class ThesisEventType(StrEnum):
    CREATED, REVISION, REVIEW, RED_FLAG_TRIGGERED, DRIFT_DETECTED, STATUS_CHANGED, HEALTH_CHANGED = (
        "CREATED", "REVISION", "REVIEW", "RED_FLAG_TRIGGERED",
        "DRIFT_DETECTED", "STATUS_CHANGED", "HEALTH_CHANGED")


class ClaimType(StrEnum):
    FACT, INTERPRETATION, ESTIMATE, WARNING = "FACT", "INTERPRETATION", "ESTIMATE", "WARNING"


class EvidenceDirection(StrEnum):
    SUPPORT, CONTRADICT, NEUTRAL = "SUPPORT", "CONTRADICT", "NEUTRAL"


class Confidence(StrEnum):
    HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"


class EvidenceSourceType(StrEnum):
    OFFICIAL_FILING, EXCHANGE, NEWS, WEB, PROVIDER, HERMES_NOTE, DOCUMENT = (
        "OFFICIAL_FILING", "EXCHANGE", "NEWS", "WEB", "PROVIDER", "HERMES_NOTE", "DOCUMENT")


class ValuationRunStatus(StrEnum):
    """ValuationRun 状态机（COMPLETED 后不可变，ts01 §4 / ts02 §6.1）。"""
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    BLOCKED_MISSING_INPUT = "BLOCKED_MISSING_INPUT"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class ValuationModelType(StrEnum):
    DCF, DDM, OWNER_EARNINGS, COMPARABLE, SCENARIO = (
        "DCF", "DDM", "OWNER_EARNINGS", "COMPARABLE", "SCENARIO")


class ValuationInputType(StrEnum):
    MARKET_PRICE, FINANCIAL_FACT, THESIS_REVISION, FX_OBSERVATION, PROVIDER_SNAPSHOT, PARQUET_DATASET, MANUAL = (
        "MARKET_PRICE", "FINANCIAL_FACT", "THESIS_REVISION", "FX_OBSERVATION",
        "PROVIDER_SNAPSHOT", "PARQUET_DATASET", "MANUAL")


class PortfolioMode(StrEnum):
    """REAL / PAPER 完全隔离（冻结规范 §25）。"""
    REAL = "REAL"
    PAPER = "PAPER"


class PortfolioStatus(StrEnum):
    ACTIVE, CLOSED = "ACTIVE", "CLOSED"


class AccountType(StrEnum):
    CASH, BROKERAGE = "CASH", "BROKERAGE"


class TransactionType(StrEnum):
    """金额符号约定（现金视角，ts02 §7.3 冻结）：BUY 负 / SELL 正 / DIVIDEND 正 / FEE 负。"""
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    FEE = "FEE"
    CASH_IN = "CASH_IN"
    CASH_OUT = "CASH_OUT"


class TransactionSource(StrEnum):
    MANUAL, HERMES_PAPER, CORPORATE_ACTION, REVERSAL, SYSTEM = (
        "MANUAL", "HERMES_PAPER", "CORPORATE_ACTION", "REVERSAL", "SYSTEM")


class ProposalType(StrEnum):
    BUY, SELL = "BUY", "SELL"


class TradeProposalStatus(StrEnum):
    """交易建议状态机（冻结规范 §25.2）：DRAFT→PROPOSED→APPROVED→EXECUTED；PROPOSED→REJECTED。"""
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"


class PeriodType(StrEnum):
    Q1, H1, Q3, FY = "Q1", "H1", "Q3", "FY"


class StatementType(StrEnum):
    INCOME, BALANCE, CASH_FLOW, OTHER = "INCOME", "BALANCE", "CASH_FLOW", "OTHER"


class HoldingSource(StrEnum):
    QUARTERLY, HALF_YEAR, ANNUAL, OTHER = "QUARTERLY", "HALF_YEAR", "ANNUAL", "OTHER"


class QuotaStatus(StrEnum):
    """QDII 额度状态：事件状态（来自公告 provenance），禁止从溢价率推断（ts01 冻结）。"""
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"
    OPEN = "OPEN"
    RESTRICTED = "RESTRICTED"
    SUSPENDED = "SUSPENDED"


class WorkspaceSubjectType(StrEnum):
    INSTRUMENT, THESIS, GENERAL = "INSTRUMENT", "THESIS", "GENERAL"


class WorkspaceStatus(StrEnum):
    OPEN, ARCHIVED = "OPEN", "ARCHIVED"


class ThreadType(StrEnum):
    RESEARCH, ANALYSIS, REVIEW = "RESEARCH", "ANALYSIS", "REVIEW"


class ThreadStatus(StrEnum):
    THREAD_OPEN, PAUSED, CLOSED = "THREAD_OPEN", "PAUSED", "CLOSED"


class FreshnessStatus(StrEnum):
    """Daily Context Freshness Contract（冻结规范 §36.2）。"""
    OK = "OK"
    WARNING = "WARNING"
    STALE = "STALE"
    FAILED = "FAILED"


class BriefStatus(StrEnum):
    DRAFT, PUBLISHED, FAILED = "DRAFT", "PUBLISHED", "FAILED"


class ActorType(StrEnum):
    HERMES, HUMAN, SYSTEM, JOB = "HERMES", "HUMAN", "SYSTEM", "JOB"


class AuditAction(StrEnum):
    CREATE, UPDATE, APPROVE, REJECT, EXECUTE, REVERSE, SUPERSEDE, STATUS_CHANGE, LOGIN = (
        "CREATE", "UPDATE", "APPROVE", "REJECT", "EXECUTE",
        "REVERSE", "SUPERSEDE", "STATUS_CHANGE", "LOGIN")


class JobType(StrEnum):
    SYNC_JOB, COMPUTE_JOB, INGESTION, BRIEF_JOB = "SYNC_JOB", "COMPUTE_JOB", "INGESTION", "BRIEF_JOB"


class JobStatus(StrEnum):
    PENDING, RUNNING, SUCCEEDED, FAILED, SUPERSEDED = (
        "PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SUPERSEDED")


class OutboxTopic(StrEnum):
    AUDIT, PROVENANCE, NOTIFICATION = "AUDIT", "PROVENANCE", "NOTIFICATION"


class OutboxStatus(StrEnum):
    PENDING, PUBLISHED, FAILED = "PENDING", "PUBLISHED", "FAILED"


class MarketCode(StrEnum):
    """trading_calendar.market。"""
    CN, US = "CN", "US"


class SessionStatus(StrEnum):
    OPEN, CLOSED, PARTIAL = "OPEN", "CLOSED", "PARTIAL"


class CorporateActionType(StrEnum):
    DIVIDEND, SPLIT, BONUS_SHARE, RIGHTS_ISSUE = "DIVIDEND", "SPLIT", "BONUS_SHARE", "RIGHTS_ISSUE"


class CorporateActionStatus(StrEnum):
    ANNOUNCED, IMPLEMENTED, ADJUSTED = "ANNOUNCED", "IMPLEMENTED", "ADJUSTED"


class AttentionItemType(StrEnum):
    """attention_items.item_type（ts02 §8.2 列举 + 预留扩展；加值需 migration）。"""
    PRICE_DROP, FILING, PE_PERCENTILE, EVENT, ANOMALY = (
        "PRICE_DROP", "FILING", "PE_PERCENTILE", "EVENT", "ANOMALY")

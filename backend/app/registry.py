# backend/app/registry.py —— 架构测试白名单的唯一事实来源（TS-08 直接 import）
TABLE_OWNER: dict[str, str] = {
    "instruments": "instruments",
    "provider_symbols": "instruments",
    "etf_profiles": "etf",
    "provenance_records": "audit",
    "market_bar_index": "market_data",
    "financial_facts": "fundamentals",
    "etf_nav_observations": "etf",
    "etf_holding_snapshots": "etf",
    "etf_metric_snapshots": "etf",
    "fx_observations": "fx",
    "theses": "thesis",
    "thesis_revisions": "thesis",
    "thesis_assumptions": "thesis",
    "thesis_reviews": "thesis",
    "thesis_red_flags": "thesis",
    "thesis_events": "thesis",
    "evidence_items": "research",
    "evidence_links": "research",
    "valuation_runs": "valuation",
    "valuation_assumptions": "valuation",
    "valuation_input_refs": "valuation",
    "portfolios": "portfolio",
    "accounts": "portfolio",
    "portfolio_transactions": "portfolio",
    "position_snapshots": "portfolio",
    "portfolio_snapshots": "portfolio",
    "target_allocations": "portfolio",
    "trade_proposals": "portfolio",
    "research_workspaces": "research",
    "research_threads": "research",
    "research_events": "research",
    "research_notes": "research",
    "daily_contexts": "briefing",
    "attention_items": "briefing",
    "daily_briefs": "briefing",
    "audit_events": "audit",
    "job_runs": "jobs",
    "outbox_events": "audit",
    "trading_calendar": "calendar",
    "corporate_actions": "corporate_actions",
}

# 白名单校验逻辑（测试实现在 TS-08）：
#   assert set(TABLE_OWNER) == set(Base.metadata.tables)          # 40 表全注册、无孤儿表
#   for table, owner in TABLE_OWNER.items():
#       cls = 找到 __tablename__ == table 的模型类
#       assert cls.__module__.startswith(f"app.{owner}.")         # 定义模块 == owner

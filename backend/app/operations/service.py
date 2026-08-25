"""标的登记后的研究初始化编排。

该服务只协调已有同步 Job 与领域服务；失败按阶段返回，已完成的阶段不会回滚。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.corporate_actions.models import CorporateAction
from app.etf.models import ETFMetricSnapshot
from app.fundamentals.models import FinancialFact
from app.instruments.models import Instrument
from app.instruments.service import InstrumentService
from app.jobs.scheduler import BackendScheduler, ComputeJobResult
from app.market_data.models import MarketBarIndex
from app.thesis.models import Thesis
from app.thesis.service import ThesisService

__all__ = ["InstrumentBootstrapService"]


class InstrumentBootstrapService:
    """把“加入标的”落实为可继续研究的最小完整状态。"""

    def run(
        self,
        session: Session,
        scheduler: BackendScheduler,
        instrument: Instrument,
        *,
        as_of: date | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        market_date = as_of or date.today()
        stages: list[dict[str, Any]] = []

        # 旧版本创建的沪深标的可能缺 Provider Symbol 或 ETFProfile。初始化时先按
        # 当前规则幂等补齐，避免同一观察池出现“老标的永远无法生成指标”的断层。
        if (
            str(instrument.instrument_type) in {"CN_EQUITY", "CN_ETF"}
            and len(instrument.symbol) == 6
            and instrument.symbol.isdigit()
        ):
            instrument, _ = InstrumentService(session).ensure_cn_instrument(
                instrument.symbol,
                instrument.name,
            )

        self._job_stage(
            stages,
            "market",
            "行情与 K 线",
            lambda: scheduler.run_market_sync_job(
                market_date, [instrument.instrument_id], force=force,
            ),
        )
        if str(instrument.instrument_type) == "CN_EQUITY":
            self._job_stage(
                stages,
                "fundamentals",
                "财务事实",
                lambda: scheduler.run_fundamental_sync_job(
                    market_date, [instrument.instrument_id], force=force,
                ),
            )
        else:
            self._job_stage(
                stages,
                "etf_metrics",
                "ETF 净值与指标",
                lambda: scheduler.run_etf_sync_job(
                    market_date, [instrument.instrument_id], force=force,
                ),
            )

        self._job_stage(
            stages,
            "corporate_actions",
            "分红与公司行动",
            lambda: scheduler.run_corporate_action_sync_job(
                market_date, [instrument.instrument_id], force=force,
            ),
        )

        session.expire_all()
        market_count = session.scalar(select(func.count()).select_from(MarketBarIndex).where(
            MarketBarIndex.instrument_id == instrument.instrument_id,
        )) or 0
        facts = list(session.scalars(select(FinancialFact).where(
            FinancialFact.instrument_id == instrument.instrument_id,
        )).all())
        self._mark_empty(
            stages,
            "market",
            empty=not market_count,
            message="数据源未返回可用行情，请检查来源配置后重试",
        )
        if str(instrument.instrument_type) == "CN_EQUITY":
            self._mark_empty(
                stages,
                "fundamentals",
                empty=not facts,
                message="数据源未返回可用财务事实，请检查 TuShare 权限后重试",
            )
        filing_keys = {
            row.source_document_id or (
                f"{row.period_end.isoformat()}@"
                f"{row.published_at.isoformat() if row.published_at else 'unknown'}"
            )
            for row in facts
        }
        if str(instrument.instrument_type) == "CN_EQUITY":
            stages.append({
                "code": "filings",
                "label": "财报公告索引",
                "status": "DONE" if filing_keys else "EMPTY",
                "items": len(filing_keys),
                "message": "已按披露时点建立索引" if filing_keys else "数据源暂未返回可见披露记录",
            })
        else:
            metric_count = session.scalar(select(func.count()).select_from(ETFMetricSnapshot).where(
                ETFMetricSnapshot.instrument_id == instrument.instrument_id,
            )) or 0
            self._mark_empty(
                stages,
                "etf_metrics",
                empty=not metric_count,
                message="数据源未生成 ETF 指标快照，请检查净值来源后重试",
            )
            stages.append({
                "code": "filings",
                "label": "公司财报",
                "status": "NOT_APPLICABLE",
                "message": "ETF 使用净值、持仓与跟踪指数资料",
            })

        thesis = session.scalar(select(Thesis).where(
            Thesis.instrument_id == instrument.instrument_id,
        ).order_by(Thesis.updated_at.desc()).limit(1))
        if thesis is None:
            latest_period = max((row.period_end for row in facts), default=None)
            thesis = ThesisService().create_thesis(
                session,
                instrument.instrument_id,
                f"{instrument.name} · 基础研究档案",
                {
                    "status": "待研究",
                    "notice": "这是系统自动建立的研究骨架，不构成投资结论或交易建议。",
                    "objective_snapshot": {
                        "as_of": market_date.isoformat(),
                        "latest_financial_period": latest_period.isoformat() if latest_period else None,
                        "available_metrics": sorted({row.metric_code for row in facts}),
                    },
                    "investment_case": [],
                    "assumptions": [],
                    "risks": [],
                    "questions": [
                        "核心盈利驱动是什么？",
                        "哪些证据会证伪当前判断？",
                        "合理估值假设和安全边际是什么？",
                    ],
                },
                authored_by="SYSTEM",
                change_reason="标的初始化",
            )
            session.commit()
            thesis_status = "CREATED"
        else:
            thesis_status = "EXISTS"
        stages.append({
            "code": "thesis",
            "label": "基础 Thesis",
            "status": "DONE",
            "message": "已建立研究骨架" if thesis_status == "CREATED" else "沿用现有研究档案",
            "thesis_id": str(thesis.thesis_id),
        })

        action_count = session.scalar(select(func.count()).select_from(CorporateAction).where(
            CorporateAction.instrument_id == instrument.instrument_id,
        )) or 0
        for stage in stages:
            if stage["code"] == "corporate_actions" and stage["status"] == "DONE":
                stage["items"] = int(action_count)
                if not action_count:
                    stage["message"] = "同步完成，当前暂无已实施权益事件"

        return {
            "instrument_id": str(instrument.instrument_id),
            "as_of": market_date.isoformat(),
            "status": (
                "PARTIAL"
                if any(row["status"] in {"FAILED", "EMPTY"} for row in stages)
                else "READY"
            ),
            "stages": stages,
        }

    @staticmethod
    def _mark_empty(
        stages: list[dict[str, Any]],
        code: str,
        *,
        empty: bool,
        message: str,
    ) -> None:
        if not empty:
            return
        stage = next((row for row in stages if row["code"] == code), None)
        if stage is not None and stage["status"] == "DONE":
            stage["status"] = "EMPTY"
            stage["items"] = 0
            stage["message"] = message

    @staticmethod
    def _job_stage(
        stages: list[dict[str, Any]],
        code: str,
        label: str,
        callback: Callable[[], ComputeJobResult],
    ) -> None:
        try:
            result = callback()
            stages.append({
                "code": code,
                "label": label,
                "status": "DONE" if result.status == "SUCCEEDED" else result.status,
                "job_run_id": str(result.job_run_id) if result.job_run_id else None,
                "items": int(result.output_version) if str(result.output_version or "").isdigit() else None,
            })
        except Exception as exc:  # 每个同步阶段独立；错误通过界面给出可重试入口
            stages.append({
                "code": code,
                "label": label,
                "status": "FAILED",
                "message": str(exc),
            })

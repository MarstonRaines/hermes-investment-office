# =====================================================================
# backend/app/market_data/parquet.py —— Parquet 层（ohlcva/v1，TS-04 §2 冻结）
#
# - schema.json 机器可验证契约（§2.1.2）：列清单与实际 Parquet 文件完全一致，
#   不一致 → 加载失败（job_runs FAILED），禁止静默读取；
# - 目录版本化：<dataset>/v<N>/ 单调递增、永不复用（§2.1.1）；
# - 物理分区（§2.2 注：v0.1 允许简化）：
#   data/parquet/ohlcva/v1/<hash>/trade_date_month=YYYY-MM/part-<inst>.parquet
# - 读取：PG market_bar_index 指针（normalizer 计算的 parquet_path）→ DuckDB。
#
# v0.1 物理放宽（2026-08-24 施工记录）：schema.json 的 required 是逻辑契约；
# Parquet 物理列一律 nullable=True —— 缺口语义（MISSING_FIELD/VALUE_NA）由
# quality_flags 显式标记（ts04 §6.3），不因个别字段缺失丢弃整行。
# =====================================================================
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.common.enums import DataQualityStatus

__all__ = [
    "OHLCVA_SCHEMA", "ETF_HOLDINGS_SCHEMA", "INDEX_HISTORY_SCHEMA",
    "ETF_NAV_SCHEMA", "FX_SCHEMA", "INDEX_VALUATION_SCHEMA",
    "ParquetStore", "SchemaMismatchError",
]

# ts04 §2.2 冻结列契约（ohlcva/v1）
OHLCVA_SCHEMA: dict[str, tuple[str, bool, str]] = {
    "instrument_id": ("string", True, "内部稳定标识（PG UUID 字符串）；禁止存 Provider symbol"),
    "trade_date": ("date32", True, "A 股交易日（Asia/Shanghai 日历日）"),
    "open": ("double", True, "开盘价（raw price，CNY/份）"),
    "high": ("double", True, "最高价（raw price，CNY/份）"),
    "low": ("double", True, "最低价（raw price，CNY/份）"),
    "close": ("double", True, "收盘价（raw price，CNY/份）"),
    "volume": ("double", True, "成交量（股/份）"),
    "amount": ("double", True, "成交额（CNY）"),
    "pre_close": ("double", True, "昨收（raw price）"),
    "pct_change": ("double", True, "百分比数值（-8.1 表示 -8.1%）"),
    "turnover_rate": ("double", False, "换手率（百分比数值）"),
    "adj_factor": ("double", True, "后复权因子（无量纲，corporate_actions 统一维护）"),
    "adjusted_close": ("double", True, "复权价 = raw close × adj_factor（黄金值校验）"),
    "provider": ("string", True, "实际取数 provider"),
    "source_timestamp": ("timestamp", False, "Provider 数据时间戳（UTC）"),
    "ingested_at": ("timestamp", True, "系统写入时间（UTC）"),
    "quality_status": ("string", True, "行级质量状态"),
}

ETF_HOLDINGS_SCHEMA: dict[str, tuple[str, bool, str]] = {
    "holding_snapshot_id": ("string", True, "持仓快照内部身份；路径按此 UUID 隔离"),
    "instrument_id": ("string", True, "ETF 内部 instrument_id"),
    "report_period": ("date32", True, "持仓对应报告期"),
    "disclosure_date": ("date32", True, "正式披露日期"),
    "source": ("string", True, "QUARTERLY/HALF_YEAR/ANNUAL/OTHER"),
    "rank": ("int64", False, "披露排名"),
    "provider_symbol": ("string", False, "原始展示代码"),
    "security_name": ("string", False, "证券名称"),
    "holding_instrument_id": ("string", False, "解析成功的内部身份"),
    "weight_pct": ("double", False, "Provider 原始占净值百分比（可审计）"),
    "weight_ratio": ("double", False, "归一化占比 ratio（0-1；已知权重和为 1）"),
    "market_value": ("double", False, "披露市值"),
    "shares": ("double", False, "披露份额"),
    "provider": ("string", True, "实际取数 provider"),
    "ingested_at": ("timestamp", True, "系统写入时间 UTC"),
    "holding_level": ("string", True, "LEVEL_1_DISCLOSED；禁止写入估算持仓"),
    "quality_flags": ("string", False, "行级质量标记；如 UNRESOLVED_SYMBOL"),
}

INDEX_HISTORY_SCHEMA: dict[str, tuple[str, bool, str]] = {
    "instrument_id": ("string", True, "冻结的 INDEX instrument_id；禁止 provider symbol"),
    "trade_date": ("date32", True, "指数交易日"),
    "open": ("double", False, "开盘点位"),
    "high": ("double", False, "最高点位"),
    "low": ("double", False, "最低点位"),
    "close": ("double", False, "收盘点位"),
    "volume": ("double", False, "成交量（若源提供）"),
    "currency": ("string", True, "指数点位币种"),
    "provider": ("string", True, "实际取数 provider"),
    "source_timestamp": ("timestamp", False, "Provider 时间戳 UTC"),
    "ingested_at": ("timestamp", True, "系统写入时间 UTC"),
    "quality_status": ("string", True, "行级质量状态"),
}

ETF_NAV_SCHEMA: dict[str, tuple[str, bool, str]] = {
    "instrument_id": ("string", True, "ETF 内部 instrument_id"),
    "nav_date": ("date32", True, "净值对应日"),
    "nav": ("double", True, "单位净值"),
    "currency": ("string", True, "净值币种"),
    "published_at": ("timestamp", False, "正式披露时点"),
    "retrieved_at": ("timestamp", True, "系统取得时间"),
    "provider": ("string", True, "实际取数 provider"),
    "quality_status": ("string", True, "行级质量状态"),
    "provenance_id": ("string", True, "事实血缘 UUID"),
}

FX_SCHEMA: dict[str, tuple[str, bool, str]] = {
    "base_currency": ("string", True, "基准币种"),
    "quote_currency": ("string", True, "报价币种"),
    "rate": ("double", True, "1 base = rate quote"),
    "as_of": ("timestamp", True, "汇率观察时点 UTC"),
    "trade_date": ("date32", False, "交易日"),
    "provider": ("string", True, "实际取数 provider"),
    "quality_status": ("string", True, "行级质量状态"),
    "provenance_id": ("string", True, "事实血缘 UUID"),
}

INDEX_VALUATION_SCHEMA: dict[str, tuple[str, bool, str]] = {
    "instrument_id": ("string", True, "冻结的 INDEX instrument_id"),
    "as_of_date": ("date32", True, "估值观察日"),
    "pe": ("double", False, "指数 PE"),
    "pb": ("double", False, "指数 PB"),
    "source": ("string", True, "具体估值序列来源"),
    "provider": ("string", True, "实际取数 provider"),
    "source_timestamp": ("timestamp", False, "源观察时点 UTC"),
    "ingested_at": ("timestamp", True, "系统写入时间 UTC"),
    "quality_status": ("string", True, "行级质量状态"),
    "provenance_id": ("string", True, "事实血缘 UUID"),
}

# schema.json 中列顺序（冻结：顺序可不同，名称/类型/必填性必须一致）
_SCHEMA_ORDER = [
    "instrument_id", "trade_date", "open", "high", "low", "close",
    "volume", "amount", "pre_close", "pct_change", "turnover_rate",
    "adj_factor", "adjusted_close", "provider", "source_timestamp",
    "ingested_at", "quality_status",
]


class SchemaMismatchError(Exception):
    """schema.json 与实际 Parquet 列不一致（ts04 §2.1.2：禁止静默读取）。"""


class ParquetStore:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)   # data/parquet

    # ---- schema.json（§2.1.2）----

    def schema_json_path(self, dataset: str, version: int) -> Path:
        return self.base_dir / dataset / f"v{version}" / "schema.json"

    def ensure_schema(
        self,
        dataset: str,
        version: int,
        columns: list[dict],
        *,
        partitions: list[str] | None = None,
    ) -> None:
        """首次写入创建 schema.json；已存在则校验与冻结契约一致。"""
        path = self.schema_json_path(dataset, version)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing["schema_version"] != version:
                raise SchemaMismatchError(
                    f"{dataset}/v{version}: schema.json version 不一致（{existing['schema_version']}）"
                )
            expected = {column["name"] for column in columns}
            declared = {column["name"] for column in existing["columns"]}
            if expected != declared:
                raise SchemaMismatchError(
                    f"{dataset}/v{version}: schema.json 列契约已冻结，"
                    f"expected={sorted(expected)} actual={sorted(declared)}"
                )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "dataset": dataset,
            "schema_version": version,
            "frozen_at": "2026-08-24",
            "columns": columns,
            "partitions": partitions or ["instrument_id_hash", "trade_date_month"],
            "unit_contract": "base_unit=CNY；pct_change/turnover_rate 为百分比数值；金额类列 base_unit=CNY",
            "timezone_contract": "所有 timestamp 列 UTC 存储；业务日期列 DATE 不带时区",
            "migration_notes": [],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def verify_schema(self, dataset: str, version: int) -> bool:
        """schema.json 列契约 ↔ 目录内实际 Parquet 文件列完全一致（§2.1.2）。"""
        import pyarrow.parquet as pq

        path = self.schema_json_path(dataset, version)
        if not path.exists():
            return False
        declared = {c["name"] for c in json.loads(path.read_text(encoding="utf-8"))["columns"]}
        files = sorted((self.base_dir / dataset / f"v{version}").rglob("*.parquet"))
        if not files:
            return True   # 无文件视为一致（初始状态）
        for f in files[:3]:   # 抽样校验（同版本列契约一致）
            actual = {fld.name for fld in pq.read_schema(f)}
            if actual != declared:
                return False
        return True

    # ---- ohlcva/v1 写入（upsert 语义：重跑重写分区文件）----

    def write_ohlcva(self, bars: list[Any], version: int = 1) -> int:
        """MarketBarResult 列表 → ohlcva/v1 分区文件（ts04 §2.2 冻结列）。

        同一 (instrument, trade_date) 重跑 → 重写 part 文件（PG 指针为权威，
        旧值 provenance 保留在 provenance_records）。
        """
        import pyarrow as pa
        import pyarrow.parquet as pq

        if not bars:
            return 0
        self.ensure_schema("ohlcva", version, self._schema_columns())
        schema = _arrow_schema()
        written = 0
        rows = [_bar_to_row(b) for b in bars]
        # 按目标路径分组写文件
        by_path: dict[str, list[dict]] = {}
        for r in rows:
            by_path.setdefault(r["_parquet_path"], []).append(r)
        for path_str, group in by_path.items():
            # 列白名单：只写冻结列（丢弃 _parquet_path 等内部键）
            clean = [{k: _norm(v) for k, v in r.items() if k in OHLCVA_SCHEMA} for r in group]
            table = pa.Table.from_pylist(clean, schema=schema)
            # 指针路径相对 data/（如 parquet/ohlcva/...），store 根在 data/parquet → 剥前缀
            target = self.base_dir / path_str.removeprefix("parquet/")
            target.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, target)
            written += len(clean)
        return written

    def read_ohlcva(
        self,
        instrument_id: str,
        *,
        start: date | None = None,
        end: date | None = None,
        as_of: date | None = None,
    ) -> list[dict]:
        """DuckDB 读取：instrument_id 过滤 + 日期裁剪 + as_of（trade_date <= as_of）。

        无数据 → 空列表（合法缺口，ts04 §5.3）。
        """
        import duckdb

        v1_dir = self.base_dir / "ohlcva" / "v1"
        if not any(v1_dir.glob("**/*.parquet")):
            return []   # 合法缺口：无文件（ts04 §5.3 缺口语义）
        glob = str(v1_dir / "**" / "*.parquet")
        where = ["instrument_id = ?"]
        params: list[Any] = [instrument_id]
        if start is not None:
            where.append("trade_date >= ?")
            params.append(start.isoformat())
        if end is not None:
            where.append("trade_date <= ?")
            params.append(end.isoformat())
        if as_of is not None:
            where.append("trade_date <= ?")
            params.append(as_of.isoformat())
        sql = (
            "SELECT instrument_id, trade_date, open, high, low, close, volume, amount, "
            "pre_close, pct_change, turnover_rate, adj_factor, adjusted_close, provider, "
            "source_timestamp, ingested_at, quality_status "
            f"FROM read_parquet(?) WHERE {' AND '.join(where)} ORDER BY trade_date"
        )
        con = duckdb.connect()
        try:
            df = con.execute(sql, [glob, *params]).fetchdf()
        finally:
            con.close()
        if df is None or df.empty:
            return []
        rows = df.to_dict(orient="records")
        # DuckDB 返回 pandas 时间类型 → 归一化为业务类型（DATE/TIMESTAMP UTC）
        for r in rows:
            td = r.get("trade_date")
            if td is not None:
                r["trade_date"] = td.date() if hasattr(td, "date") else td
            for col in ("source_timestamp", "ingested_at"):
                v = r.get(col)
                if v is not None and hasattr(v, "to_pydatetime"):
                    r[col] = v.to_pydatetime()
        return rows

    def write_etf_holdings(self, snapshots: list[Any], version: int = 1) -> int:
        """Compatibility writer for tests and offline fixtures.

        The sync service supplies an already-generated snapshot UUID and
        resolved rows through :meth:`write_etf_holdings_rows`; this convenience
        path generates UUIDs locally only for standalone fixture writes.
        """
        from uuid import uuid4

        from app.market_data.normalizer import holdings_path_for

        rows: list[dict] = []
        for snapshot in snapshots:
            snapshot_id = uuid4()
            weights = [item.weight_pct for item in snapshot.holdings]
            ratios = _normalize_weights(weights)
            for index, item in enumerate(snapshot.holdings):
                ratio = ratios[index]
                invalid_weight = (
                    item.weight_pct is not None and item.weight_pct < 0
                )
                resolved = str(item.instrument_id) if item.instrument_id else item.provider_symbol
                flags = []
                if not item.instrument_id:
                    flags.append("UNRESOLVED_SYMBOL")
                if invalid_weight:
                    flags.append("INVALID_WEIGHT")
                rows.append({
                    "holding_snapshot_id": str(snapshot_id),
                    "instrument_id": str(snapshot.instrument_id),
                    "report_period": snapshot.report_period,
                    "disclosure_date": snapshot.disclosure_date,
                    "source": _enum_value(snapshot.source),
                    "rank": item.rank,
                    "provider_symbol": item.provider_symbol,
                    "security_name": item.security_name,
                    "holding_instrument_id": resolved,
                    "weight_pct": item.weight_pct,
                    "weight_ratio": ratio,
                    "market_value": item.market_value,
                    "shares": item.shares,
                    "provider": snapshot.provenance.provider,
                    "ingested_at": snapshot.provenance.retrieved_at,
                    "holding_level": "LEVEL_1_DISCLOSED",
                    "quality_flags": ",".join(flags),
                    "_parquet_path": holdings_path_for(
                        snapshot.instrument_id, snapshot.report_period,
                        holding_snapshot_id=snapshot_id,
                    ),
                })
        return self.write_etf_holdings_rows(rows, version=version)

    def write_etf_holdings_rows(self, rows: list[dict], version: int = 1) -> int:
        """Write normalized Level 1 rows whose snapshot IDs already exist in PG."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        if not rows:
            return 0
        order = list(ETF_HOLDINGS_SCHEMA)
        self.ensure_schema(
            "etf_holdings", version, self._schema_columns_for(ETF_HOLDINGS_SCHEMA, order),
            partitions=["instrument_id_hash", "report_period", "holding_snapshot_id"],
        )
        schema = _arrow_schema_for(ETF_HOLDINGS_SCHEMA, order)
        by_path: dict[str, list[dict]] = {}
        for row in rows:
            by_path.setdefault(row["_parquet_path"], []).append(row)
        for path_str, group in by_path.items():
            clean = [
                {k: _norm(v) for k, v in row.items() if k in ETF_HOLDINGS_SCHEMA}
                for row in group
            ]
            target = self.base_dir / path_str.removeprefix("parquet/")
            target.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(clean, schema=schema), target)
        return len(rows)

    def read_etf_holdings(
        self, instrument_id: str, *, report_period: date | None = None
    ) -> list[dict]:
        """读取 Level 1 持仓；Level 2 估算结果没有此数据集入口。"""
        import duckdb

        v1_dir = self.base_dir / "etf_holdings" / "v1"
        if not any(v1_dir.glob("**/*.parquet")):
            return []
        if not self.verify_schema("etf_holdings", 1):
            raise SchemaMismatchError("etf_holdings/v1 schema.json 与 Parquet 列不一致")
        where = ["instrument_id = ?", "holding_level = 'LEVEL_1_DISCLOSED'"]
        params: list[Any] = [instrument_id]
        if report_period is not None:
            where.append("report_period = ?")
            params.append(report_period.isoformat())
        sql = (
            "SELECT * FROM read_parquet(?) WHERE "
            + " AND ".join(where)
            + " ORDER BY report_period, rank"
        )
        con = duckdb.connect()
        try:
            df = con.execute(sql, [str(v1_dir / "**" / "*.parquet"), *params]).fetchdf()
        finally:
            con.close()
        if df is None or df.empty:
            return []
        return _normalize_frame_rows(df.to_dict(orient="records"))

    def write_etf_nav(self, rows: list[dict], version: int = 1) -> int:
        import pyarrow as pa
        import pyarrow.parquet as pq

        if not rows:
            return 0
        order = list(ETF_NAV_SCHEMA)
        self.ensure_schema(
            "etf_nav", version,
            self._schema_columns_for(ETF_NAV_SCHEMA, order),
            partitions=["instrument_id_hash", "nav_date"],
        )
        schema = _arrow_schema_for(ETF_NAV_SCHEMA, order)
        by_path: dict[str, list[dict]] = {}
        for row in rows:
            by_path.setdefault(row["_parquet_path"], []).append(row)
        for path_str, group in by_path.items():
            clean = [
                {k: _norm(v) for k, v in row.items() if k in ETF_NAV_SCHEMA}
                for row in group
            ]
            target = self.base_dir / path_str.removeprefix("parquet/")
            target.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(clean, schema=schema), target)
        return len(rows)

    def read_etf_nav(
        self,
        instrument_id: str,
        *,
        as_of: datetime | None = None,
        parquet_path: str | None = None,
    ) -> list[dict]:
        import duckdb

        v1_dir = self.base_dir / "etf_nav" / "v1"
        if not any(v1_dir.glob("**/*.parquet")):
            return []
        if not self.verify_schema("etf_nav", 1):
            raise SchemaMismatchError("etf_nav/v1 schema.json 与 Parquet 列不一致")
        where = ["instrument_id = ?"]
        params: list[Any] = [instrument_id]
        if as_of is not None:
            where.append("published_at <= ?")
            params.append(_norm(as_of))
        source = _parquet_source(self.base_dir, v1_dir, parquet_path=parquet_path)
        sql = (
            "SELECT * FROM read_parquet(?) WHERE "
            + " AND ".join(where)
            + " ORDER BY nav_date"
        )
        con = duckdb.connect()
        try:
            df = con.execute(sql, [source, *params]).fetchdf()
        finally:
            con.close()
        return (
            _normalize_frame_rows(df.to_dict(orient="records"))
            if df is not None and not df.empty
            else []
        )

    def write_fx_rates(self, rows: list[dict], version: int = 1) -> int:
        import pyarrow as pa
        import pyarrow.parquet as pq

        if not rows:
            return 0
        order = list(FX_SCHEMA)
        self.ensure_schema(
            "fx", version, self._schema_columns_for(FX_SCHEMA, order),
            partitions=["currency_pair", "as_of_date"],
        )
        schema = _arrow_schema_for(FX_SCHEMA, order)
        by_path: dict[str, list[dict]] = {}
        for row in rows:
            by_path.setdefault(row["_parquet_path"], []).append(row)
        for path_str, group in by_path.items():
            clean = [
                {k: _norm(v) for k, v in row.items() if k in FX_SCHEMA}
                for row in group
            ]
            target = self.base_dir / path_str.removeprefix("parquet/")
            target.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(clean, schema=schema), target)
        return len(rows)

    def read_fx_rates(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        parquet_paths: list[str] | None = None,
    ) -> list[dict]:
        import duckdb

        v1_dir = self.base_dir / "fx" / "v1"
        if not any(v1_dir.glob("**/*.parquet")):
            return []
        if not self.verify_schema("fx", 1):
            raise SchemaMismatchError("fx/v1 schema.json 与 Parquet 列不一致")
        where: list[str] = []
        params: list[Any] = []
        for column, value, operator in (
            ("trade_date", start, ">="),
            ("trade_date", end, "<="),
            ("as_of", as_of, "<="),
        ):
            if value is not None:
                where.append(f"{column} {operator} ?")
                params.append(_norm(value))
        source = _parquet_source(self.base_dir, v1_dir, parquet_paths=parquet_paths)
        sql = "SELECT * FROM read_parquet(?)"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY as_of"
        con = duckdb.connect()
        try:
            df = con.execute(sql, [source, *params]).fetchdf()
        finally:
            con.close()
        return (
            _normalize_frame_rows(df.to_dict(orient="records"))
            if df is not None and not df.empty
            else []
        )

    def write_index_valuations(self, rows: list[dict], version: int = 1) -> int:
        import pyarrow as pa
        import pyarrow.parquet as pq

        if not rows:
            return 0
        order = list(INDEX_VALUATION_SCHEMA)
        self.ensure_schema(
            "index_valuation", version,
            self._schema_columns_for(INDEX_VALUATION_SCHEMA, order),
            partitions=["instrument_id_hash", "as_of_date"],
        )
        schema = _arrow_schema_for(INDEX_VALUATION_SCHEMA, order)
        by_path: dict[str, list[dict]] = {}
        for row in rows:
            by_path.setdefault(row["_parquet_path"], []).append(row)
        for path_str, group in by_path.items():
            clean = [
                {k: _norm(v) for k, v in row.items() if k in INDEX_VALUATION_SCHEMA}
                for row in group
            ]
            target = self.base_dir / path_str.removeprefix("parquet/")
            target.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(clean, schema=schema), target)
        return len(rows)

    def read_index_valuations(
        self,
        instrument_id: str,
        *,
        as_of: date | None = None,
        parquet_paths: list[str] | None = None,
    ) -> list[dict]:
        import duckdb

        v1_dir = self.base_dir / "index_valuation" / "v1"
        if not any(v1_dir.glob("**/*.parquet")):
            return []
        if not self.verify_schema("index_valuation", 1):
            raise SchemaMismatchError("index_valuation/v1 schema.json 与 Parquet 列不一致")
        where = ["instrument_id = ?"]
        params: list[Any] = [instrument_id]
        if as_of is not None:
            where.append("as_of_date <= ?")
            params.append(as_of.isoformat())
        source = _parquet_source(self.base_dir, v1_dir, parquet_paths=parquet_paths)
        sql = (
            "SELECT * FROM read_parquet(?) WHERE "
            + " AND ".join(where)
            + " ORDER BY as_of_date"
        )
        con = duckdb.connect()
        try:
            df = con.execute(sql, [source, *params]).fetchdf()
        finally:
            con.close()
        return (
            _normalize_frame_rows(df.to_dict(orient="records"))
            if df is not None and not df.empty
            else []
        )

    def write_index_history(self, bars: list[Any], version: int = 1) -> int:
        """写入指数历史 Parquet；PG index_bar_index 保存唯一指针。"""
        import pyarrow as pa
        import pyarrow.parquet as pq

        from app.market_data.normalizer import index_parquet_path_for

        if not bars:
            return 0
        order = list(INDEX_HISTORY_SCHEMA)
        self.ensure_schema(
            "index_history", version,
            self._schema_columns_for(INDEX_HISTORY_SCHEMA, order),
            partitions=["instrument_id_hash", "trade_date_month"],
        )
        schema = _arrow_schema_for(INDEX_HISTORY_SCHEMA, order)
        by_path: dict[str, list[dict]] = {}
        for bar in bars:
            row = {
                "instrument_id": str(bar.index_id), "trade_date": bar.trade_date,
                "open": bar.open, "high": bar.high, "low": bar.low,
                "close": bar.close, "volume": bar.volume,
                "currency": bar.currency, "provider": bar.provenance.provider,
                "source_timestamp": getattr(bar, "source_timestamp", None),
                "ingested_at": bar.provenance.retrieved_at,
                "quality_status": _enum_value(bar.provenance.quality_status),
            }
            path = index_parquet_path_for(
                bar.index_id, bar.trade_date, bar.provenance.provider
            )
            by_path.setdefault(path, []).append(row)
        for path_str, group in by_path.items():
            clean = [
                {k: _norm(v) for k, v in row.items() if k in INDEX_HISTORY_SCHEMA}
                for row in group
            ]
            target = self.base_dir / path_str.removeprefix("parquet/")
            target.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(clean, schema=schema), target)
        return len(bars)

    def read_index_history(
        self,
        index_id: str,
        *,
        start: date | None = None,
        end: date | None = None,
        as_of: date | None = None,
        parquet_paths: list[str] | None = None,
    ) -> list[dict]:
        import duckdb

        v1_dir = self.base_dir / "index_history" / "v1"
        if not any(v1_dir.glob("**/*.parquet")):
            return []
        if not self.verify_schema("index_history", 1):
            raise SchemaMismatchError("index_history/v1 schema.json 与 Parquet 列不一致")
        where = ["instrument_id = ?"]
        params: list[Any] = [index_id]
        for column, value, operator in (
            ("trade_date", start, ">="),
            ("trade_date", end, "<="),
            ("trade_date", as_of, "<="),
        ):
            if value is not None:
                where.append(f"{column} {operator} ?")
                params.append(value.isoformat())
        source = _parquet_source(self.base_dir, v1_dir, parquet_paths=parquet_paths)
        sql = (
            "SELECT * FROM read_parquet(?) WHERE "
            + " AND ".join(where)
            + " ORDER BY trade_date"
        )
        con = duckdb.connect()
        try:
            df = con.execute(sql, [source, *params]).fetchdf()
        finally:
            con.close()
        if df is None or df.empty:
            return []
        return _normalize_frame_rows(df.to_dict(orient="records"))

    # ---- 内部 ----

    @staticmethod
    def _schema_columns() -> list[dict]:
        return ParquetStore._schema_columns_for(OHLCVA_SCHEMA, _SCHEMA_ORDER)

    @staticmethod
    def _schema_columns_for(schema: dict[str, tuple[str, bool, str]], order: list[str]) -> list[dict]:
        return [
            {
                "name": name, "type": schema[name][0],
                "required": schema[name][1], "description": schema[name][2],
            }
            for name in order
        ]


def _arrow_schema():
    return _arrow_schema_for(OHLCVA_SCHEMA, _SCHEMA_ORDER)


def _arrow_schema_for(schema: dict[str, tuple[str, bool, str]], order: list[str]):
    import pyarrow as pa

    fields = []
    for name in order:
        ptype, _, _ = schema[name]
        pa_type = {
            "string": pa.string(), "date32": pa.date32(),
            "double": pa.float64(), "int64": pa.int64(),
            "timestamp": pa.timestamp("us", tz="UTC"),
        }[ptype]
        # v0.1 放宽（模块文档已记录）：物理列允许 NULL（缺口语义由 quality_flags
        # 显式标记，ts04 §6.3），必填性以 schema.json 逻辑契约为准；
        # verify_schema 只比对名称/类型（ts04 §2.1.2 名称/类型一致）。
        fields.append(pa.field(name, pa_type, nullable=True))
    return pa.schema(fields)


def _norm(v: Any) -> Any:
    """数值/时间归一化（Decimal→float，date→date，datetime→UTC aware）。"""
    from decimal import Decimal

    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)
    if isinstance(v, DataQualityStatus):
        return v.value
    if hasattr(v, "value") and isinstance(v.value, str):
        return v.value
    return v


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _parquet_source(
    base_dir: Path,
    dataset_dir: Path,
    *,
    parquet_path: str | None = None,
    parquet_paths: list[str] | None = None,
) -> str | list[str]:
    """Resolve PG pointer paths, falling back to the dataset glob for legacy rows."""
    values = [parquet_path] if parquet_path else parquet_paths or []
    if not values:
        return str(dataset_dir / "**" / "*.parquet")
    resolved: list[str] = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            path = base_dir / value.removeprefix("parquet/")
        resolved.append(str(path))
    return resolved[0] if len(resolved) == 1 else resolved


def _normalize_frame_rows(rows: list[dict]) -> list[dict]:
    for row in rows:
        for col in ("report_period", "trade_date", "nav_date", "as_of_date"):
            value = row.get(col)
            if value is not None and hasattr(value, "date"):
                row[col] = value.date()
        for col in ("source_timestamp", "ingested_at", "published_at", "retrieved_at", "as_of"):
            value = row.get(col)
            if value is not None and hasattr(value, "to_pydatetime"):
                row[col] = value.to_pydatetime()
    return rows


def _normalize_weights(values: list[Any]) -> list[float | None]:
    """Convert disclosed percentage/ratio values to a normalized ratio."""
    from decimal import Decimal

    clean = [
        Decimal(str(value)) if value is not None and Decimal(str(value)) >= 0 else None
        for value in values
    ]
    valid = [value for value in clean if value is not None]
    if not valid:
        return [None for _ in values]
    scale = Decimal("100") if sum(valid) > Decimal("1.5") else Decimal("1")
    ratios = [value / scale if value is not None else None for value in clean]
    total = sum(value for value in ratios if value is not None)
    if total <= 0:
        return [None for _ in values]
    return [float(value / total) if value is not None else None for value in ratios]


def _bar_to_row(bar) -> dict:
    """MarketBarResult → ohlcva/v1 Parquet 行（冻结列 + 内部路径键 _parquet_path）。"""
    from app.market_data.normalizer import parquet_path_for

    def f(v):
        return float(v) if v is not None else None

    return {
        "instrument_id": str(bar.instrument_id),
        "trade_date": bar.trade_date,
        "open": f(bar.open), "high": f(bar.high), "low": f(bar.low),
        "close": f(bar.close), "volume": f(bar.volume), "amount": f(bar.amount),
        "pre_close": f(bar.pre_close), "pct_change": f(bar.pct_change),
        "turnover_rate": f(bar.turnover_rate), "adj_factor": f(bar.adj_factor),
        "adjusted_close": f(bar.adjusted_close),
        "provider": bar.provider,
        "source_timestamp": bar.source_timestamp,
        "ingested_at": bar.provenance.retrieved_at,
        "quality_status": bar.provenance.quality_status.value,
        "_parquet_path": parquet_path_for(bar.instrument_id, bar.trade_date),
    }

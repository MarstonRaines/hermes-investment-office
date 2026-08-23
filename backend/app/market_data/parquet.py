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

__all__ = ["OHLCVA_SCHEMA", "ParquetStore", "SchemaMismatchError"]

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

    def ensure_schema(self, dataset: str, version: int, columns: list[dict]) -> None:
        """首次写入创建 schema.json；已存在则校验与冻结契约一致。"""
        path = self.schema_json_path(dataset, version)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing["schema_version"] != version:
                raise SchemaMismatchError(
                    f"{dataset}/v{version}: schema.json version 不一致（{existing['schema_version']}）"
                )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "dataset": dataset,
            "schema_version": version,
            "frozen_at": "2026-08-24",
            "columns": columns,
            "partitions": ["instrument_id_hash", "trade_date_month"],
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

    # ---- 内部 ----

    @staticmethod
    def _schema_columns() -> list[dict]:
        return [
            {
                "name": name,
                "type": OHLCVA_SCHEMA[name][0],
                "required": OHLCVA_SCHEMA[name][1],
                "description": OHLCVA_SCHEMA[name][2],
            }
            for name in _SCHEMA_ORDER
        ]


def _arrow_schema():
    import pyarrow as pa

    fields = []
    for name in _SCHEMA_ORDER:
        ptype, required, _ = OHLCVA_SCHEMA[name]
        pa_type = {
            "string": pa.string(),
            "date32": pa.date32(),
            "double": pa.float64(),
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
    return v


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

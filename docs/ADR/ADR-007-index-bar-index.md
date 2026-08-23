# ADR-007：index_history 的 PG 指针表（index_bar_index）

> 状态：**Accepted（2026-08-23）**
>
> 关联：TS-04 §2.5（index_history/v1 契约声明）、parquet-schema.md §5、TS-02（market_bar_index 同构设计）
>
> 类型：M1 施工回流（ts04 开放问题 #2 落定）

---

## 1. 背景

TS-04 定义了 `index_history/v1` Parquet 数据集（指数点位与估值历史），但未冻结其 PG 侧指针表。M1 施工推进到 index 数据（Yahoo ^GSPC/^NDX、乐咕估值、TuShare index_daily）时，需要与 `market_bar_index` 同构的"存在性 + 质量 + 路径"查询层——没有它，读取只能全目录扫描（违背 TS-02 §4.2 的指针层设计）。

## 2. 决策

### D1：新增 `index_bar_index` 表（与 market_bar_index 同构）

```sql
CREATE TABLE index_bar_index (
    index_bar_id      UUID PRIMARY KEY,
    instrument_id     UUID NOT NULL REFERENCES instruments,   -- INDEX 类型
    trade_date        DATE NOT NULL,                          -- 对应市场交易日
    provider          TEXT NOT NULL,                          -- 实际取数 provider
    source_timestamp  TIMESTAMPTZ,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    quality_status    TEXT NOT NULL,
    provenance_id     UUID NOT NULL REFERENCES provenance_records,
    parquet_path      TEXT NOT NULL,                          -- index_history/v1/...
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (instrument_id, trade_date, provider)
);
```

- 归属模块：**market_data**（与 market_bar_index 一致）；
- 与 `market_bar_index` 同构（INDEX 行情与股票/ETF 行情共用同一套读取路径，Engine 无差别消费）；
- 索引估值（PE/PB 列）随点位同文件（乐咕/FRED 源），指针表不做区分。

### D2：读取路径

```text
PG index_bar_index（as_of + instrument_id）→ parquet_path → DuckDB index_history/v1 → 点位/估值
```

与 market_bar_index 完全一致；TS-02 §4.2 的"禁止绕过指针扫描目录"规则同样适用。

### D3：数据源映射

| 数据 | 写入 provider | 说明 |
|---|---|---|
| A 股指数点位（沪深300 等）| tushare(index_daily) primary / akshare_sina fallback | INDEX instrument |
| 美股指数点位（^GSPC/^NDX）| yahoo | 对应 INDEX instrument（如 provider_symbols 映射）|
| 指数估值（PE/PB）| legulegu（A 股）/ FRED 或 Shiller（美股，PENDING）| 同文件追加列（pe/pb）|

## 3. 影响

- ts02 表数 40 → **41**（+1；watchlists 2 张另计，共 43 业务表）；
- migration：`NNNN_index_bar_index.py`（依赖 provenance_records/job_runs/instruments 已建）；
- ORM：`app/market_data/models.py` 增 `IndexBarIndex`；TABLE_OWNER 注册；
- TS-08：ARCH-DB 白名单更新（41 表）；GOLD-PIT 增指数 as_of 场景；
- M1 施工：index 同步 job 按本表落库。

## 4. 关联

- TS-04 开放问题 #2（本 ADR 落定）
- ADR-005（网络分流：yahoo env / legulegu direct）

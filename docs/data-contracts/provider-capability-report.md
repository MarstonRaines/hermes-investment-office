# M0.5 Data Feasibility Spike 报告（provider-capability-report）

> 状态：**COMPLETED（2026-08-23 实测）**
>
> 对应：冻结规范 §47 M0.5 Data Feasibility Spike / TS-05 §9 Spike 回流契约
>
> 结论回流：本报告 → `provider-capability.md` 更新 + ADR-005（Provider 网络层分流策略）
>
> 实测环境：Apple Silicon Mac（Rosetta shell）、Python 3.12、系统级透明代理 + 显式代理 127.0.0.1:7892

---

## 1. 执行摘要

M0.5 Spike 全部 9 项（S1-S9）实测完成。**核心结论：v0.1 数据源策略整体可行，无阻塞项。**

| 项 | 结论 | 关键事实 |
|---|---|---|
| S1 TuShare | ✅ 2000 积分档全覆盖 | 10 个核心接口全通；ETF 行情走 `fund_daily`（非 `daily`）|
| S2 AkShare | ⚠️ 部分可用 | eastmoney 源本机直连被阻（代理可通）；新浪源/同花顺源可用作 fallback |
| S3 Yahoo | ✅ 可用 | ^GSPC/^NDX/USDCNY=X 全通（依赖代理环境）|
| S4 ETF 持仓 | ✅ 可用 | `fund_portfolio_hold_em` 季报持仓（Level 1 穿透源）|
| S5 财务单位 | ✅ 元 | TuShare 三大报表数值单位 = 元（base_unit=CNY 直接映射）|
| S6 Index Valuation | ✅ 首选锁定 | **乐咕乐股（legulegu）**：沪深300 PE 5194 行、中证500 4765 行历史；免自聚合 |
| S7 FX | ✅ 双源 | Yahoo `USDCNY=X` + FRED `DEXCHUS`（6.7118 vs 6.7412 交叉验证）|
| S8 NAV/Quota | ✅ 数据可得 | `fund_nav`（nav_date/ann_date 双日期）+ `fund_etf_spot_em`（折溢价率）；quota 公告半自动 |
| S9 交易日历 | ✅ 可用 | 新浪 `tool_trade_date_hist_sina` 8797 行（2000-2026）|

**三个需要回流 ADR/文档的关键发现**：

1. **ETF 行情接口是 `fund_daily`**（TuShare），`daily` 对 ETF 返回空——直接影响 Provider 实现；
2. **eastmoney push2his 本机直连被网络层拦截**（curl 直连 HTTP 000 / 走 127.0.0.1:7892 代理 HTTP 200），而 TuShare/新浪/乐咕直连正常——Provider 网络层必须支持 per-provider 代理分流；
3. **AkShare A 股日线 fallback 应改用新浪源**（`stock_zh_a_daily` 已验证），不依赖 eastmoney。

---

## 2. S1-S9 逐项实测

### S1 — TuShare 2000 积分档（✅ 全通）

token 实测（贵州茅台 600519 抽样，2026-08-23）：

| 接口 | 结果 | 备注 |
|---|---|---|
| stock_basic | ✅ 5549 行 | A 股全市场 |
| daily | ✅ 15 行/月 | 个股日线 |
| adj_factor | ✅ 15 行/月 | 复权因子 |
| income / balancesheet / cashflow | ✅ 各 1 行/期 | 三大报表 |
| fina_indicator | ✅ 1 行/期 | 财务指标 |
| index_daily | ✅ 15 行/月 | 指数行情 |
| index_weight | ✅ 300 行 | ⚠️ 数据延迟发布（7/31 有、8/21 无）|
| fund_basic | ✅ 2905 行 | 基金列表 |
| **fund_daily** | ✅ 15 行/月 | **ETF 场内行情（关键：非 daily）** |
| fund_nav | ✅ 14 行/月 | **nav_date + ann_date 双日期（QDII T+1 直接对应）** |

限流：单次实测未触发；TuShare 官方限制为每分钟调用频率（积分档位决定），M1 实现时按官方频控配置，spike 结论：**无需升级积分**。

### S2 — AkShare（⚠️ 部分可用，fallback 调整）

| 接口 | 结果 | 备注 |
|---|---|---|
| stock_zh_a_hist（eastmoney） | ❌ 直连失败 | push2his.eastmoney.com 本机被阻；curl 走代理 127.0.0.1:7892 成功（HTTP 200）|
| **stock_zh_a_daily（新浪源）** | ✅ 15 行 | **A 股日线 fallback 首选**（含前复权 qfq）|
| fund_etf_hist_em（eastmoney） | ✅ 15 行 | ETF 行情（偶发可用，代理时更稳）|
| fund_open_fund_info_em | ✅ 3198 行 | 基金净值 |
| stock_financial_abstract_ths（同花顺） | ✅ 103 行 | 财务摘要备用 |
| tool_trade_date_hist_sina | ✅ 8797 行 | 交易日历（S9）|
| fund_portfolio_hold_em | ✅ 22 行 | 季报持仓（S4）|

**fallback 链调整建议（写回 provider-capability.md）**：

```text
CN_DAILY_QUOTE:   tushare(daily) → akshare_sina(stock_zh_a_daily) → DATA_UNAVAILABLE
CN_ETF_QUOTE:     tushare(fund_daily) → akshare_eastmoney(fund_etf_hist_em) → akshare_sina → DATA_UNAVAILABLE
```

### S3 — Yahoo（✅ 可用，依赖代理环境）

| 标的 | 结果 | 最新值（2026-08-21）|
|---|---|---|
| ^GSPC 标普500 | ✅ 23 行/月 | 7674.37 |
| ^NDX 纳指100 | ✅ 23 行/月 | 29308.86 |
| USDCNY=X | ✅ 6 行 | 6.7118 |

> 网络依赖：本机系统透明代理环境下可用；**Provider 层需对 Yahoo/FRED 提供代理配置插槽**（见 §4）。

### S4 — ETF 持仓穿透（✅ Level 1 源锁定）

- `fund_portfolio_hold_em(symbol, date)`：季度报告持仓，含**股票代码/名称/占净值比例/持股数/持仓市值/季度**——直接对应 ts02 `etf_holding_snapshots`（report_period/disclosure_date 语义需在实现中从接口元数据补充）
- Level 2（估算 exposure）由 ETF Engine 基于 Level 1 + 指数近似计算（TS-06 §4）

### S5 — 财务单位（✅ 元）

TuShare 三大报表数值单位实测：茅台 2025 年报 `revenue=168838102514.79`（≈1688 亿元 ✓）。

```text
四元组映射：original_value=168838102514.79, original_unit='元',
           normalized_value=168838102514.79, normalized_unit='CNY'
（单位系数 1:1；无 万元/亿元 转换需求 —— TuShare 已统一为元）
```

> 结论：Financial Unit Normalization Spike（§47.2）**降级为简单规则**：TuShare 报表单位恒为元；仅 AkShare/其他源需四元组转换。

### S6 — Index Valuation（✅ 首选来源锁定：乐咕乐股）

| 源 | 结果 | 覆盖 |
|---|---|---|
| **乐咕乐股 stock_index_pe_lg** | ✅ 沪深300 PE 5194 行 / 中证500 4765 行 | 20+ 年历史；静态/滚动/TTM 多口径 |
| FRED（美股候选） | ✅ 已验证 | 宏观序列（DEXCHUS 等）|
| 自聚合方案 | 不需要 | 乐咕覆盖 A 股主要宽基；成本最高方案废弃 |

> 结论（冻结规范 §47.1 回流）：A 股指数 PE/PB 历史 → **乐咕乐股 primary**；美股指数估值 → Shiller PE（multpl.com，Spike 后 PENDING）或 FRED 派生；**spike 前禁止伪造历史分位**的约束解除（真实数据源已确认）。

### S7 — FX（✅ 双源交叉验证）

| 源 | 结果 | 最新值 |
|---|---|---|
| Yahoo USDCNY=X | ✅ | 6.7118（2026-08-21）|
| FRED DEXCHUS | ✅ | 6.7412（2026-08-14）|

两源存在口径/时点差异（~0.4%），**FUND_NAV 归因时以对应对账日为准**；primary 建议 Yahoo（日频），FRED 作权威对账（S7 结论：双源保留，Yahoo primary / FRED authority）。

### S8 — NAV / Quota（✅ 数据可得；quota 半自动）

- NAV：TuShare `fund_nav`（nav_date + ann_date 分离 ✓）+ AkShare `fund_open_fund_info_em`
- 折溢价：AkShare `fund_etf_spot_em`（"基金折价率"列；纳指 ETF 实测折价 -8.7% ~ -24.9%）
- **quota_status（外汇额度/限购）**：无现成结构化接口 → 维持"基金管理人公告人工/半自动录入 + provenance"方案（事件状态，禁止推断）；**PENDING（不影响 v0.1 主链路，标注 LOW 风险）**

### S9 — 交易日历（✅）

新浪 `tool_trade_date_hist_sina`：8797 行（2000-01-04 ~ 2026 全量 A 股交易日）。人工校准 + 年度更新机制（Provider 层封装 `is_trading_day/next_trading_day`）。美股日历：Yahoo 交易日派生或 PENDING 补充源。

---

## 3. 网络环境与代理（2026-08-23 实测）

用户 Mac 常年运行代理（**端口 7892**），当前为系统透明代理模式：

| 目标 | 直连 | 显式代理 127.0.0.1:7892 |
|---|---|---|
| TuShare / 新浪 / 乐咕 / 同花顺 | ✅ | — |
| Yahoo / FRED | ✅（经透明代理）| ✅ |
| **eastmoney push2his** | ❌ HTTP 000（0.28s 被拒）| ✅ HTTP 200（0.15s）|

**工程结论（ADR-005 输入）**：

1. Provider 网络层必须支持 **per-provider 代理配置**（`provider-capability.yaml` 增加 `network.proxy` 字段：`direct` / `http://127.0.0.1:7892`）；
2. requests 环境变量代理（HTTPS_PROXY）对 AkShare 内部 session 未生效 → **实现时用 `session.proxies` 显式传入**；
3. 国内 Provider 默认直连（TuShare/新浪/乐咕），eastmoney 类配置代理；
4. 代理变更（关闭/更换端口）不影响 Provider 接口契约，只改配置。

---

## 4. 回流清单（写入 provider-capability.md / ADR）

| 回流项 | 内容 | 去向 |
|---|---|---|
| R1 | ETF 行情 = TuShare `fund_daily`（非 daily）| provider-capability.md CN_ETF_QUOTE 行 |
| R2 | A 股日线 fallback = AkShare 新浪源 | provider-capability.md CN_DAILY_QUOTE fallback 链 |
| R3 | A 股 Index Valuation primary = 乐咕乐股 | provider-capability.md INDEX_VALUATION 行（解除 SPIKE-PENDING）|
| R4 | FX primary = Yahoo USDCNY=X；authority = FRED DEXCHUS | provider-capability.md FX 行（解除 SPIKE-PENDING）|
| R5 | 财务单位规则 = TuShare 恒元（四元组系数 1:1）| data-contracts/unit-normalization.md |
| R6 | 交易日历源 = 新浪 + 人工校准 | provider-capability.md TRADING_CALENDAR 行 |
| R7 | Per-provider 代理配置 | **ADR-005**（Provider 网络层分流策略）|
| R8 | quota_status 保持半自动 + PENDING | provider-capability.md QUOTA_STATUS 行 |
| R9 | index_weight 延迟发布 → freshness 容忍 | data-contracts/freshness-contract.md |

---

## 5. 仍 PENDING（不阻塞 M0.5 完成）

1. **quota_status 结构化源**（基金公司公告 → 半自动录入；v0.1 LOW 风险）
2. **美股指数估值源**（Shiller PE 实测；乐咕只覆盖 A 股——美股 QDII 分析用 FRED/Shiller）
3. **eastmoney 代理配置实测**（M1 Provider 实现时验证 session.proxies 路径）
4. 限流深度实测（TuShare 高频调用边界，M1 联调时）

---

## 6. 结论

**M0.5 Spike COMPLETED**。v0.1 数据源策略冻结为：

```text
A 股行情/财务:  TuShare（primary）→ AkShare 新浪源（fallback）
ETF 行情/NAV:   TuShare fund_daily/fund_nav（primary）→ AkShare（fallback）
美股指数:       Yahoo ^GSPC/^NDX（primary，代理环境）
A 股指数估值:   乐咕乐股（primary）→ AkShare（fallback）
FX:            Yahoo USDCNY=X（primary）→ FRED DEXCHUS（authority）
交易日历:       新浪 + 人工校准
ETF 持仓:      AkShare fund_portfolio_hold_em（Level 1）
```

无阻塞项；M1 Data Layer 可开工。Spike 结论已回流（R1-R9）；ADR-005 待创建（Provider 网络层）。

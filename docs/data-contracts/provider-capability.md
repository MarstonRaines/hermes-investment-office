# Provider Capability Matrix（provider-capability.md）

> 状态：**FROZEN（v1.0，2026-08-23；M0.5 Spike 全量回流）**
>
> 依据：冻结规范 §11.1（Capability Matrix 冻结）、TS-05 §4、provider-capability-report.md（R1-R9）
>
> 维护规则：Provider 变更/积分变化/接口停更 → 更新本文件 + ADR 记录（冻结规范 §11.1）；YAML 注册表 ↔ 代码注册 ↔ 实现三方一致（架构测试 A4）

---

## 1. 数据域 × Provider 矩阵（v0.1 冻结）

图例：**P** = primary；**F** = fallback；**A** = authority（权威冲突源）；`—` = 不涉及

| 数据域 | TuShare | AkShare-eastmoney | AkShare-新浪 | 同花顺(ths) | Yahoo | FRED | 乐咕乐股 | 巨潮/官方 | 基金管理人 |
|---|---|---|---|---|---|---|---|---|---|
| CN_DAILY_QUOTE（个股日线）| **P** | — | **F** | — | — | — | — | — | — |
| CN_ETF_QUOTE（ETF 场内行情）| **P**（fund_daily）| **F** | — | — | — | — | — | — | — |
| ADJ_FACTOR（复权因子）| **P** | — | — | — | — | — | — | — | — |
| FINANCIAL_STATEMENTS（三大报表）| **P** | — | — | **F** | — | — | — | **A** | — |
| FINA_INDICATOR（财务指标）| **P** | — | — | **F** | — | — | — | — | — |
| INDEX_QUOTE（A 股指数行情）| **P** | — | **F** | — | — | — | — | — | — |
| INDEX_WEIGHT（指数成分权重）| **P** | — | — | — | — | — | — | — | — |
| US_INDEX_QUOTE（美股指数点位）| — | — | — | — | **P** | — | — | — | — |
| INDEX_VALUATION（指数 PE/PB 历史）| — | — | — | — | — | **F**（美股）| **P**（A 股）| — | — |
| FUND_NAV（基金净值）| **P** | — | — | — | — | — | — | — | **A** |
| FUND_HOLDINGS（季报持仓）| — | **P** | — | — | — | — | — | — | **A** |
| ETF_PREMIUM（折溢价率）| — | **P**（fund_etf_spot_em）| — | — | — | — | — | — | — |
| FX（USD/CNY）| — | — | — | — | **P**（USDCNY=X）| **A**（DEXCHUS）| — | — | — |
| MACRO_SERIES（宏观）| — | — | — | — | — | **P** | — | — | — |
| QUOTA_STATUS（QDII 额度）| — | — | — | — | — | — | — | — | **P**（公告，半自动）|
| TRADING_CALENDAR（交易日历）| — | — | **P**（新浪）| — | — | — | — | — | — |
| OFFICIAL_FILING（公告/财报文档）| — | — | — | — | — | — | — | **P**（巨潮）| **P** |

## 2. 每域 Fallback 链（冻结）

```text
CN_DAILY_QUOTE:    tushare(daily) → akshare_sina(stock_zh_a_daily) → DATA_UNAVAILABLE
CN_ETF_QUOTE:      tushare(fund_daily) → akshare_eastmoney(fund_etf_hist_em) → akshare_sina → DATA_UNAVAILABLE
ADJ_FACTOR:        tushare(adj_factor) → corporate_actions 人工校准 → CONFLICT
FINANCIAL_STATEMENTS: tushare → akshare_ths(财务摘要) → DATA_UNAVAILABLE（权威=巨潮/交易所）
FINA_INDICATOR:    tushare → akshare_ths → DATA_UNAVAILABLE
INDEX_QUOTE:       tushare(index_daily) → akshare_sina(index) → DATA_UNAVAILABLE
INDEX_WEIGHT:      tushare(index_weight) → DATA_UNAVAILABLE（⚠️ 数据延迟发布 T+N）
US_INDEX_QUOTE:    yahoo(^GSPC/^NDX) → 备用源（PENDING）→ DATA_UNAVAILABLE
INDEX_VALUATION:   legulegu（A 股）→ akshare → DATA_UNAVAILABLE；美股：FRED/Shiller（PENDING）
FUND_NAV:          tushare(fund_nav) → akshare → DATA_UNAVAILABLE
FUND_HOLDINGS:     akshare_eastmoney(fund_portfolio_hold_em) → 基金管理人报告 → DATA_UNAVAILABLE
ETF_PREMIUM:       akshare_eastmoney(fund_etf_spot_em) → DATA_UNAVAILABLE
FX:                yahoo(USDCNY=X) → DATA_UNAVAILABLE（权威对账：FRED DEXCHUS）
MACRO_SERIES:      fred → DATA_UNAVAILABLE
QUOTA_STATUS:      基金管理人公告（人工/半自动录入）→ UNKNOWN（禁止推断）
TRADING_CALENDAR:  akshare_sina(tool_trade_date_hist_sina) + 人工校准 → DATA_UNAVAILABLE
OFFICIAL_FILING:   巨潮（cninfo）→ 交易所 → DATA_UNAVAILABLE
```

## 3. Provider 质量与限制（冻结）

| Provider | 质量基线 | 已知限制 | 网络模式（ADR-005）|
|---|---|---|---|
| TuShare | 0.96（核心接口）| 积分制（2000 档已确认全覆盖）；接口限频；index_weight 延迟发布 | direct |
| AkShare-eastmoney | 0.90 | **本机直连被阻**（push2his）；需代理 127.0.0.1:7892；接口偶发不稳 | proxy |
| AkShare-新浪 | 0.90 | 字段覆盖较 eastmoney 少（无 amount 部分场景）| direct |
| 同花顺(ths) | 0.88 | 财务摘要口径非标准报表 | direct |
| Yahoo | 0.93 | 非官方接口；依赖代理环境；历史数据可能不完整 | env |
| FRED | 0.98 | 日频有限；DEXCHUS 时点 T+1 | env |
| 乐咕乐股 | 0.95 | 覆盖 A 股主要宽基/行业指数；数据延迟 T+1 | direct |
| 巨潮 | 0.97 | 无结构化行情；PDF 文档获取 | direct |
| 基金管理人 | 0.98 | 无统一 API；公告半自动 | direct |

> 质量基线为初始建议值（M0.5 Spike 后），实际 quality_score 由 Provenance 服务按四维（完整性/时效性/一致性/来源可靠性）计算（TS-04 §6），本表仅作为无实测时的初始值。

## 4. SPIKE-PENDING（v1.0 未冻结项，均不阻塞 M1 主链路）

| 项 | 状态 | 影响 |
|---|---|---|
| 美股指数估值源（Shiller PE）| PENDING | QDII 指数 PE 分位（LOW 优先级）|
| QUOTA_STATUS 结构化源 | PENDING（半自动）| QDII 额度状态（LOW）|
| Yahoo 备用源 | PENDING | US_INDEX_QUOTE 单源风险（MED）|
| 限流深度参数 | PENDING（M1 联调实测）| 抓取频率配置 |

## 5. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-08-23 | 冻结；M0.5 Spike R1-R9 全量回流（ETF 走 fund_daily、新浪源 fallback、乐咕 primary、FX 双源、交易日历新浪）|

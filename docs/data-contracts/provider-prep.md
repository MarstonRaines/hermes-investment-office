# Hermes Investment Office 数据源准备清单（provider-prep）

> 状态：**执行中**（随 M0.5 Spike 结果回流更新）
>
> 版本：v1.0（2026-08-23）
>
> 关联文档：TS-05 Provider Architecture（§4 Capability Matrix、§9 Spike 回流契约）、冻结规范 §12 数据源策略、§47 M0.5 Data Feasibility Spike
>
> 目的：列出 v0.1 全部数据源、每项需要用户准备的动作（注册/积分/密钥/网络验证）、以及 Spike 验证脚本大纲。**本文档不是契约**——契约以 provider-capability.md 为准；本文档只负责"准备什么"。

---

## 1. 数据源全景（v0.1 涉及 8 个源）

| # | 数据源 | 类型 | 负责数据域 | 是否需要准备 | 状态 |
|---|---|---|---|---|---|
| 1 | **TuShare** | A 股结构化数据 | CN_DAILY_QUOTE、CN_ETF_QUOTE、ADJ_FACTOR、FINANCIAL_STATEMENTS、INDEX_WEIGHT | **注册 + API token + 积分** | ✅ **2000 积分档已确认（用户）** |
| 2 | **FRED** | 宏观/指数估值 | MACRO_SERIES、US Index Valuation（候选） | **注册 + API key** | 待申请 |
| 3 | **AkShare** | A 股备用 + 基金 | 全数据域 fallback、FUND_NAV、FUND_HOLDINGS | 无需注册（pip 库） | 免准备 |
| 4 | **Yahoo Finance** | 美股指数/汇率 | US_INDEX_QUOTE（^GSPC/^NDX）、FX（USDCNY） | 无需注册（yfinance 库） | ⚠️ 网络可达性待验证 |
| 5 | **巨潮资讯网（cninfo）** | 官方披露 | OFFICIAL_FILING（财报/公告）、权威冲突源 | 无需注册 | 免准备 |
| 6 | **中证指数官网 / 沪深交易所** | A 股指数估值/日历 | INDEX_VALUATION（候选）、TRADING_CALENDAR、权威对账 | 无需注册 | Spike 验证（S6/S9） |
| 7 | **Shiller PE（multpl.com）** | 美股指数估值 | US Index Valuation（候选） | 无需注册 | Spike 验证（S6） |
| 8 | **基金管理人公告/官网** | QDII 专属 | FUND_NAV（官方）、FUND_HOLDINGS（季报）、QUOTA_STATUS（额度公告） | 无需注册 | Spike 验证（S8） |

**未来扩展（v0.1 不准备）**：SEC EDGAR（仅 QDII 底层美股穿透需要时启用）、推送通道（§39 Non-goal）。

---

## 2. 需要准备的源

### 2.1 TuShare —— A 股数据主力（最优先）

- **注册**：https://tushare.pro/register
- **获取 API token**：登录后「个人主页 → 接口TOKEN」复制，存环境变量（**禁止进代码/进 git**）
- **积分**：**2000 积分档（用户已确认，2026-08-23）**
  - 2000 积分覆盖多数核心接口：日线行情（daily）、复权因子（adj_factor）、财务指标（fina_indicator）、财务报表（income / balancesheet / cashflow）、股票列表、指数基本信息等；
  - 部分更高门槛接口（分钟级、部分另类数据）v0.1 不需要；
  - **仍须 M0.5 Spike 逐接口实测**（S1）：确认每个 v0.1 必需接口在该档位的实际可用范围、频率限制与数据质量，结果写入 provider-capability.md。
- **v0.1 从 TuShare 获取的数据域**：A 股个股日行情、A 股 ETF 日行情（含 QDII ETF 场内行情）、复权因子、财务三大报表与关键指标、指数成分/权重（Index Valuation 自聚合候选，spike 决定是否必需）。

**TuShare 接口实测清单（S1，M0.5 Spike）**：

```text
[x] stock_basic        —— 股票列表（✅ 2026-08-23 实测 5549 行）
[x] daily              —— 个股日线（✅ 15 行/月）
[x] adj_factor         —— 复权因子（✅ 15 行/月）
[x] income / balancesheet / cashflow —— 三大报表（✅ 各 1 行/期）
[x] fina_indicator     —— 财务指标（✅ 1 行/期）
[x] index_daily        —— 指数行情（✅ 15 行/月）
[x] index_weight       —— 指数成分权重（✅ 300 行；⚠️ 数据延迟发布，当日数据需等 T+N）
[x] fund_basic         —— 基金列表（✅ 2905 行）
[x] fund_daily         —— ETF 场内日线（✅ 15 行/月；**ETF 行情必须走 fund_daily，daily 对 ETF 返回空**）
[x] fund_nav           —— 基金净值（✅ 14 行/月；nav_date 与 ann_date 分离，匹配 QDII T+1 时序）
[ ] 限流实测           —— 每分钟/每日调用额度，超限行为（待测）
```

> **S1 预验证结论（2026-08-23，token 实测）**：2000 积分档覆盖全部 v0.1 核心接口。
> 关键工程事实：①ETF 场内行情接口为 `fund_daily`（非 `daily`）；②`fund_nav` 天然携带 `nav_date`（估值日）与 `ann_date`（公告日）双日期，与 ts01 QDII 四日期建模直接对应；③`index_weight` 数据延迟发布（7/31 有、8/21 无），freshness 契约需容忍该延迟。

### 2.2 FRED —— 宏观与美股指数估值

- **注册 + API key 申请**：先注册 FRED 账号（https://fredaccount.stlouisfed.org/，免费），登录后到 **API Keys 页**（https://fredaccount.stlouisfed.org/apikeys）点击 Request API key 生成（key 为 32 位小写字母数字）；每个应用申请独立 key
- **API key 存环境变量**（`backend/.env` 的 `HERMES_FRED_API_KEY`，禁止进代码/进 git/进 .env.example）
- **v0.1 用途**：宏观序列（MACRO_SERIES primary，已冻结）；美股指数估值（Index PE/PB，S6 候选源，spike 后定）
- **网络注意**：国内直连可能受限，需在 Spike 中验证（见 §4）

---

## 3. 免注册源（无需任何准备）

| 源 | 接入方式 | 说明 |
|---|---|---|
| AkShare | `pip install akshare` | 开源库，A 股主备 + 基金 NAV/持仓接口；字段稳定性待 Spike（S2） |
| Yahoo Finance | `pip install yfinance` | 指数点位 ^GSPC/^NDX、USDCNY=X；非官方接口，无 key；网络验证见 §4 |
| 巨潮资讯网 | HTTP 公开接口 | 财报/公告 PDF 下载（docId 体系），权威冲突源；PDF 进 Raw Evidence Store |
| 中证指数官网 / 交易所 | HTTP 公开页面/接口 | 指数估值、交易日历、权威对账；可结构化程度待 Spike（S6/S9） |
| Shiller PE | HTTP 公开页面 | 美股长周期估值（CAPE），候选源 |
| 基金管理人 | 公告/官网 PDF | QDII 持仓穿透、净值、外汇额度公告；结构化渠道待 Spike（S8） |

---

## 4. 网络可达性检查（M0.5 Spike 第一优先级）

以下源在国内网络环境下可能受限，**Spike 启动第一天先验证**：

```text
[ ] yfinance 拉取 ^GSPC / ^NDX 历史点位（>5 年）——失败则准备代理方案或替代源
[ ] FRED API 请求（示例：DEXCHUS 美元人民币）——同上
[ ] tushare.pro API 直连（应无问题，确认超时表现）
[ ] 巨潮资讯接口连通性
[ ] AkShare 基金接口连通性（ak.fund_etf_spot_em 等）
```

> 若 Yahoo/FRED 直连不可用：TS-05 的 Provider 网络层设计需要提前知道（代理配置插槽），在 M0.5 报告回流时通过 ADR 记录，不要拖到 M1。

---

## 5. M0.5 Spike 验证脚本大纲（S1–S9）

> 详细契约见 TS-05 §9；此处仅列可执行清单，供准备环境。

```text
S1  TuShare 积分实测（§2.1 接口清单）
S2  AkShare 关键接口稳定性（行情/基金持仓字段 7 日抽样）
S3  Yahoo ^GSPC/^NDX 可得性（§4 网络检查）
S4  ETF 持仓披露获取（季报 → 结构化）
S5  财务单位归一化（元/万元/亿元 → 四元组，§47.2）
S6  Index Valuation Source（A 股：中证/交易所/AkShare/自聚合；美股：FRED/Shiller/自聚合）→ 首选来源 + 覆盖区间 + 频率
S7  FX 数据源（Yahoo USDCNY=X / FRED DEXCHUS）→ 选定生产 provider
S8  NAV/Quota 官方源渠道（巨潮/管理人网站结构化获取）
S9  Trading Calendar 来源（交易所公开日历 + 人工校准）
```

**输出**：`provider-capability-report` → 回流 provider-capability.md + ADR（Spike 结论不得与实现脱节，施工纪律第 19 条）。

---

## 6. 准备状态跟踪（Checklist）

| # | 项目 | 状态 | 备注 |
|---|---|---|---|
| 1 | TuShare 注册 + token | ✅ **2026-08-23 已配置** | 已入 `backend/.env`（gitignore 确认），连通性验证中 |
| 2 | TuShare 积分档位 | ✅ **2000 积分档（用户确认 2026-08-23）** | 关键接口大概率可用，逐接口实测待 S1 |
| 3 | FRED 注册 + API key | ⬜ | 入口：fredaccount.stlouisfed.org/apikeys（先注册 FRED 账号） |
| 4 | AkShare 安装（本机 venv） | ⬜ | `pip install akshare` |
| 5 | yfinance 安装 + 网络验证 | ⬜ | 见 §4 |
| 6 | 巨潮连通性验证 | ⬜ | 见 §4 |
| 7 | Spike 脚本骨架 | ⬜ | M0 施工期准备 |

---

## 7. 安全约定

- 所有 API token / key / 积分凭证**只走环境变量或 secret 文件**，不进代码、不进 git（TS-05 §3 配置层约定）；
- `provider-capability.yaml` 只记录"能访问哪些数据、质量如何"，**不记录凭证**；
- 凭证泄漏事故演练（git 历史清理）在 M0 施工前完成一次。

---

## 8. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-08-23 | 初版；记录 TuShare 2000 积分档（用户确认） |

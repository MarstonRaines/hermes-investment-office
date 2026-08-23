# Dashboard 开源参考调研（dashboard-open-source-reference）

> 状态：**COMPLETED（2026-08-23 网络调研）**
>
> 输入：dashboard-design-reference.md（信息架构）
>
> 原则：项目"不 Fork / 代码不迁移"（Benchmark §13）；所有引用前核对许可证；本文档只做"参考/复用"决策，不引入第三方代码依赖到 Backend

---

## 1. 调研结论总表

| 项目 | 定位 | 许可证 | 参考价值 | 复用方式 |
|---|---|---|---|---|
| **[Ghostfolio](https://github.com/ghostfolio/ghostfolio)**（9.2k★）| 开源财富管理 dashboard（Angular+NestJS，自托管，隐私优先）| AGPL-3.0 | **最高**：组合分析、风险面板、完整投资 dashboard 信息架构 | 参考 IA/功能（不抄代码；AGPL 传染）|
| **[Wealthfolio](https://github.com/wealthfolio/wealthfolio)**（8k★）| 本地优先投资 tracker（Tauri/React，桌面+移动+Docker）| 开源（需核对）| 高：账户/活动/绩效心智模型、现代 UI 质感 | 参考 UX 心智模型 |
| **[Portfolio Performance](https://www.portfolio-performance.info/en/)** | Java 桌面投资组合管理（长期项目）| EPL | 高：收益计算（TWR/IRR）、报告体系深度 | 参考功能清单（验证我们 TWR 口径决策）|
| **[OpenBB](https://github.com/OpenBB-finance/OpenBB)** | 开源金融数据平台 + Workspace（研究终端）| 需核对（商业混合）| 中高：数据统一接口思想与 TS-05 Provider 层同构；Workspace 界面布局 | 参考架构思想 + Workspace 布局 |
| **lightweight-charts**（[TradingView](https://github.com/tradingview/lightweight-charts)）| **K 线图库（Apache-2.0，TradingView 官方维护）** | Apache-2.0 | **K 线组件直接解决方案** | ✅ **可直接采用**（前端组件，非后端依赖）|
| lightweight-charts-python（[louisnw01](https://github.com/louisnw01/lightweight-charts-python)）| lightweight-charts 的 Python/Streamlit 包装 | 需核对 | Streamlit 阶段 K 线组件 | ✅ 候选（或直接用 HTML 组件嵌入）|
| [financial-dashboard-streamlit](https://github.com/0xZee/financial-dashboard-streamlit) | Streamlit + yfinance 财务 dashboard | 需核对 | Streamlit 布局/组件组织参考 | 参考结构（数据源我们自有 API，不用其 yfinance 逻辑）|
| visualfolio / scani-oss | 个人投资分析 dashboard | 需核对 | 低：小项目 | 浏览参考 |

---

## 2. 组件级复用方案（按我们的技术栈分层）

### 2.1 Streamlit 阶段（v0.1）

| 组件 | 方案 | 说明 |
|---|---|---|
| **K 线图** | **TradingView lightweight-charts**（Apache-2.0，npm/ESM）+ `components.html` 嵌入，或 lightweight-charts-python 包装 | 专业 K 线交互（缩放/十字线/成交量）；Apache-2.0 无传染；与未来 .app 共用同一图表库（WebView）|
| 普通图表（净值曲线/估值带/暴露图）| **plotly**（MIT，Streamlit 原生支持 `st.plotly_chart`）| 标准选择；估值带/暴露/回撤等非 K 线图 |
| 表格/列表 | Streamlit 原生 `st.dataframe` / `st.data_editor`（只读用 dataframe）| 观察池列表行、持仓明细 |
| 状态灯/徽章 | 原生 HTML/CSS 小部件（自研 ~50 行）| 不引入组件库，视觉语义（绿/黄/红/灰）自实现 |

### 2.2 .app 阶段（未来，ADR-004）

| 组件 | 方案 |
|---|---|
| K 线 | Swift Charts（系统框架，用户偏好最小依赖）自定义 candle 类型，或 WKWebView 嵌 lightweight-charts（两阶段共用同一图表库）|
| 全部图表 | **Swift Charts**（iOS 16+/macOS 13+ 系统框架）|
| 表格/列表 | SwiftUI List/Table |

> 关键决策：**K 线交互复杂度是分叉点**。若需求停留在"看 K 线+缩放"，Swift Charts 足够；若未来要画线工具/复杂指标叠加，WebView + lightweight-charts 更省力。v0.1 先按 lightweight-charts 定（Streamlit 阶段验证交互需求），.app 阶段再定 Swift Charts vs WebView。

---

## 3. 信息架构借鉴点（映射到我们的设计）

### 3.1 从 Ghostfolio 借鉴（对标我们的四个导航）

| Ghostfolio 板块 | 我们的落位 | 借鉴点 |
|---|---|---|
| Dashboard（净资产/持仓/市场概览）| 今日页 | "一屏总览 + 明细链接"的信息密度；资产分配 donut 图 |
| Holdings（持仓明细+绩效）| 持仓页 | 持仓行信息层级（数量/成本/现价/盈亏/仓位）；分红视图 |
| Portfolio Analyzer（风险/暴露/因子）| 持仓页风险面板 | 暴露/集中度可视化形态 |
| Activities（交易流水）| 回顾页（后置）| 流水列表 + 过滤 |
| X-ray（穿透分析）| 持仓页风险面板 | **与我们 ETF 穿透（Level 1/2）同构**——强烈参考其"ETF 成分穿透展示"交互 |

### 3.2 从 Wealthfolio 借鉴

- **本地优先 + 隐私**：与我们 127.0.0.1 + Tailscale 路线一致（外部验证）；
- **账户→活动→绩效**心智模型：其"通过交易活动计算绩效"与我们 Transaction Ledger → Position 派生完全同构（外部验证我们的 ledger-first 设计）；
- UI 质感：现代配色（其 HN 评论区好评的 Flexoki 色调）可作为视觉风格候选参考。

### 3.3 从 OpenBB 借鉴

- **统一数据接口**：其 "connect once, consume everywhere" 与我们的 Provider 层 + REST/MCP 双出口同构——我们已冻结此架构，无需改动；
- Workspace 布局：多面板研究工作区（watchlist + 图表 + 数据联动）可作为**观察池详情页**的布局灵感（但我们 v0.1 简化：列表→详情页，不做多面板工作区）。

### 3.4 从 Portfolio Performance 借鉴

- 其收益计算口径（TWR/IRR、多币种、分红再投）可用来**验证 ts06 Portfolio Engine 的 TWR 主口径决策**（黄金值交叉验证来源之一）；
- 报告导出（PDF）为远期功能参考（v0.1 不做）。

---

## 4. 许可证与合规（个人自用场景）

> **使用场景决策（2026-08-23 用户确认）：本项目为个人自用（非商用、不对外分发/提供服务）。**

| 项目 | 许可证 | 个人自用下的使用方式 | 义务触发条件（如未来变化）|
|---|---|---|---|
| lightweight-charts | Apache-2.0 | ✅ 直接引入（保留 NOTICE）| 分发时保留许可证 |
| plotly | MIT | ✅ 直接引入 | 分发时保留许可证 |
| Ghostfolio | AGPL-3.0 | ✅ **可复用代码**（自用不触发 copyleft）| 若未来**向第三方提供服务**（如远程开放给他人），AGPL 网络条款触发 → 修改版须开源 |
| Wealthfolio | 待核对 | ✅ 可复用（待核对后确认）| 按最终核对结果 |
| Portfolio Performance | EPL | ✅ **可复用代码**（自用）| 若未来分发修改版，EPL 文件级 copyleft（修改文件须开源）|
| OpenBB | 待核对（商业混合）| ✅ 可复用（待核对后确认）| 按最终核对结果 |

**注意事项（写进施工纪律）**：

1. 复用 AGPL/EPL 代码时，**保留其源文件许可证头与版权声明**（注释标注来源 + 许可证）；
2. 复用代码与自研代码**分目录隔离**（如 `vendor/ghostfolio-xray/`），便于日后若场景变化（商用/分发）可整体摘除；
3. 每次实际引入代码前，仍按 Benchmark §13 完成逐仓库 License 审计并记录于 ADR（许可证文本随版本变动）；
4. 未来远程化（ADR-004 阶段 2+）若仅本人设备访问，不构成"向公众提供服务"，AGPL 不触发；**若开放给第三方用户则必须先做合规评估**。

---

## 5. 建议采用清单（M7 施工输入）

```text
Tier 1（直接引入，许可证安全）：
  [ ] TradingView lightweight-charts（Apache-2.0）—— K 线组件（Streamlit components.html 嵌入）
  [ ] plotly（MIT）—— 净值曲线/估值带/暴露图

Tier 2（可复用代码，个人自用场景）：
  [ ] Ghostfolio（AGPL，自用可复用）—— Holdings/Portfolio Analyzer/X-ray 组件与页面代码
  [ ] Wealthfolio（待核对）—— 绩效/活动 UI 组件
  [ ] Portfolio Performance（EPL，自用可复用）—— 收益计算实现（TWR/IRR，黄金值交叉验证）
  [ ] OpenBB（待核对）—— Workspace 前端组件（如引入）

Tier 3（思想/设计参考）：
  [ ] OpenBB 统一数据接口（已同构，无需动作）
  [ ] 各项目的信息架构与 UX 模式（开发时按需截图参考）
```

## 5.1 开发时参考仓库索引（正式参考入口）

| 仓库 | URL | 开发时重点看 |
|---|---|---|
| Ghostfolio | https://github.com/ghostfolio/ghostfolio | `apps/client/src/app/pages/`（Holdings/Analyzer/X-ray 页面）、`libs/portfolio/`（收益计算）|
| Wealthfolio | https://github.com/wealthfolio/wealthfolio | 账户/活动/绩效 UI、Tauri 结构 |
| Portfolio Performance | https://github.com/portfolio-performance/portfolio （官方仓库域名见官网）| `name.abuchen.portfolio`（收益/报表核心）|
| OpenBB | https://github.com/OpenBB-finance/OpenBB | Workspace 前端、Provider 抽象 |
| lightweight-charts | https://github.com/tradingview/lightweight-charts | K 线 API、示例 |
| lightweight-charts-python | https://github.com/louisnw01/lightweight-charts-python | Streamlit 嵌入方案 |
| financial-dashboard-streamlit | https://github.com/0xZee/financial-dashboard-streamlit | Streamlit 布局参考 |
| visualfolio | https://github.com/benvigano/visualfolio | Django+Plotly 个人财务 dashboard 参考 |

> 开发时引用规则：①进入对应仓库目录前先读 LICENSE（核对版本）；②复用代码入 `vendor/<项目名>/` 目录并保留许可证头；③参考点记录到本文件（追加"复用记录"章节）；④AGPL/EPL 复用的源文件标注来源与许可证。

## 6. 对设计文档的增量

1. dashboard-design-reference §10 组件清单更新：K 线 = lightweight-charts；图表 = plotly；
2. 观察池详情页"关系"标签：参考 Ghostfolio X-ray 的穿透可视化（ETF 底层暴露）；
3. 持仓页风险面板：参考 Ghostfolio Analyzer 的暴露图形态。

## 7. 待办（M7 施工时执行）

- [ ] lightweight-charts 许可证/版本复核（Apache-2.0 NOTICE 保留）
- [ ] lightweight-charts-python 或 components.html 嵌入方案选型
- [ ] Wealthfolio/OpenBB 许可证最终核对（如引入任何代码）
- [ ] Ghostfolio X-ray 交互细节截图收集（视觉参考素材）

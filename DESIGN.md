---
name: "Hermes Investment Office"
description: "证据优先、中文优先的本地个人投资运营驾驶舱"
colors:
  primary: "#0b6ffb"
  action: "#075fcc"
  blue-text: "#075fcc"
  primary-soft: "#eaf3ff"
  success: "#0aa66d"
  success-text: "#067647"
  danger: "#e5484d"
  danger-text: "#b42318"
  warning: "#dc8b13"
  warning-text: "#854d0e"
  indicator-ma30: "#7c68ee"
  canvas: "#f5f7fa"
  surface: "#ffffff"
  surface-soft: "#f8fafc"
  sidebar: "#fbfcfe"
  border: "#e5e9f0"
  border-strong: "#d8dee8"
  text: "#172033"
  text-muted: "#667085"
  on-accent: "#ffffff"
  dark-canvas: "#0d1119"
  dark-surface: "#141a24"
  dark-surface-soft: "#111721"
  dark-sidebar: "#101620"
  dark-border: "#273142"
  dark-border-strong: "#344054"
  dark-text: "#edf2f7"
  dark-text-muted: "#9aa6b6"
  dark-primary: "#4d96ff"
  dark-action: "#1769d2"
  dark-blue-text: "#78aaff"
  dark-primary-soft: "#162a47"
  dark-success: "#2bc48a"
  dark-success-text: "#2bc48a"
  dark-danger: "#ff6b70"
  dark-danger-text: "#ff8589"
  dark-warning: "#f0a94a"
  dark-warning-text: "#f0b45c"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "clamp(1.45rem, 2vw, 1.9rem)"
    fontWeight: 730
    lineHeight: 1.15
    letterSpacing: "-0.035em"
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "0.94rem"
    fontWeight: 680
    lineHeight: 1.35
    letterSpacing: "-0.015em"
  metric:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "clamp(1.25rem, 1.75vw, 1.7rem)"
    fontWeight: 710
    lineHeight: 1.18
    letterSpacing: "-0.025em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "0.82rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "normal"
rounded:
  surface: "10px"
  container: "8px"
  control: "7px"
  badge: "4px"
  round: "50%"
spacing:
  compact: "0.28rem"
  inline: "0.55rem"
  control: "0.75rem"
  card: "0.95rem"
  panel: "1rem"
  page-x: "2.1rem"
components:
  button-primary:
    backgroundColor: "{colors.action}"
    textColor: "{colors.on-accent}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    height: "36px"
  navigation-active:
    backgroundColor: "{colors.action}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.control}"
    padding: "0.38rem 0.7rem"
    height: "42px"
  freshness-banner:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.control}"
    padding: "0.55rem 0.75rem"
    height: "38px"
  metric-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.metric}"
    rounded: "{rounded.surface}"
    padding: "0.95rem 3.15rem 0.8rem 0.95rem"
    height: "132px"
  state-card:
    backgroundColor: "{colors.surface-soft}"
    textColor: "{colors.text}"
    rounded: "{rounded.container}"
    padding: "1rem"
    height: "190px"
  severity-info:
    backgroundColor: "{colors.primary-soft}"
    textColor: "{colors.blue-text}"
    typography: "{typography.label}"
    rounded: "{rounded.badge}"
    padding: "0.18rem 0.3rem"
---

# Hermes Investment Office 设计系统

## Overview

**Creative North Star: “冷静、可审计的私人投资办公室”**

这是一个用于判断与操作的金融 Dashboard，不是营销页、券商终端或交易机器人。设计以冷白纸面、克制蓝绿信号、细描边金融卡片、统一线性图标和高密度中文表格构成熟悉的专业工作台；视觉表达必须让系统状态、数据新鲜度、证据缺口与人工责任先于装饰。

设计契约固定为 `mode=operate`、`direction=canon`，设计种子为 `c375a27a`。核心叙事是一眼判断“今天是否可行动”，然后从同一界面完成观察、研究复核、Hermes 对话和手工账本工作。业务事实与账本动作只消费 Backend REST；“问 Hermes”只连接本机 Hermes 会话网关，前端不制造数据、计算、持仓或审计事实。

**Key Characteristics:**

- 中文优先，语气冷静、准确、证据优先；必要缩写保留中文语境。
- Mac 大屏优先，移动端使用可收起的导航抽屉；明暗主题跟随系统。
- 高信息密度但层级明确；摘要在前，来源、运行细节和原文渐进披露。
- 蓝色承担主操作、选择和主图形；绿、黄、红承担明确状态，文字与图形分用对比度合格的语义 token。
- 四个正式表面是“今日、标的、组合、问 Hermes”；观察池、研究历史和回顾分别进入标的筛选、事件时间线和历史会话，真实组合变化始终表达为人工行为。

## Colors

整体由冷白或深墨中性色表面与少量高辨识度信号色组成。颜色服务于快速扫描、状态判断和操作反馈，不承担情绪化装饰。

### Primary

- **操作蓝**（`action` / 暗色 `dark-action`）：当前导航、主按钮与需要明确操作归属的位置。
- **图形蓝**（`primary` / 暗色 `dark-primary`）：组合净值趋势、MA5、状态点和较大图形；浅蓝底使用 `primary-soft` / `dark-primary-soft`。
- **语义蓝文字**（`blue-text` / 暗色 `dark-blue-text`）：小字号 INFO、OK 与选中标签文字。

### Secondary

- **可信绿**（`success` / `dark-success`）：新鲜度通过和健康状态点；小字号成功状态文字使用 `success-text` / `dark-success-text`。
- **告警琥珀**（`warning` / `dark-warning`）：WARNING、STALE 状态点和较大告警图形；小字号告警文字使用 `warning-text` / `dark-warning-text`。
- **失败红**（`danger` / `dark-danger`）：FAILED 状态点和请求错误；小字号错误文字使用 `danger-text` / `dark-danger-text`。

### 行情方向色

- 股票、ETF、组合收益及盈亏统一遵循 A 股习惯：上涨或正收益用红色，下跌或负收益用绿色，零值使用中性色。
- 行情方向与系统状态是两套语义：系统健康仍用绿色，系统失败仍用红色；不得用行情方向色表达服务健康度。
- 标的行情使用不复权日 K：阳线与上涨影线为红色，阴线与下跌影线为绿色；MA5 使用图形蓝、MA20 使用告警琥珀、MA30 使用指标紫 `#7c68ee`。
- 组合净值主趋势线保持图形蓝；涨跌方向同时保留正负号和数值，不能只靠红绿传意。

### Neutral

- **画布、表面与侧栏**：使用 `canvas`、`surface`、`surface-soft`、`sidebar` 及对应 `dark-*` token 建立层级。
- **正文与次级文本**：正文使用 `text`，时间、说明和元数据使用 `text-muted`；暗色模式映射到对应 `dark-*` token。
- **边界**：常规分隔使用 `border`，交互控件和需要更明确轮廓的位置使用 `border-strong`。

**The Contrast-by-Role Rule.** 亮色主题保留明亮的 `primary`、`success`、`danger`、`warning` 给状态点与图形；小字号语义文字必须分别使用 `blue-text` / `action`（#075fcc）、`success-text`（#067647）、`danger-text`（#b42318）、`warning-text`（#854d0e）。它们对白底对比度依次为 5.97、5.69、6.57、6.85:1；白字对操作蓝同为 5.97:1。暗色主操作使用 `dark-action`（#1769d2），白字对比度为 5.27:1；暗色语义文字沿用已实现的 `dark-blue-text`、`dark-success-text`、`dark-danger-text`、`dark-warning-text`。

**The System Theme Rule.** 由 `prefers-color-scheme` 切换整套 token，不提供与系统相冲突的局部主题；暗色模式主要靠明度层级和描边，不靠发光效果。

**The Semantic Color Rule.** 状态色必须同时出现状态文本、数值符号或图形形态；任何关键信息都不得只靠红绿区分。

## Typography

**Display Font:** Apple 系统无衬线字族，中文回退为 PingFang SC / Microsoft YaHei。

**Body Font:** 与 Display 共用系统字族。
**Label Font:** 与正文共用字族，金融数字启用等宽数字。

**Character:** 字体不建立额外品牌戏剧性，而是追求 Mac 原生、清晰、紧凑。中文是默认阅读语言；产品名、REST、PIT、TWR、WACC 等保留英文时，附近文案必须提供中文语境。

### Hierarchy

- **Display**（`display`）：页面标题，只在页面起点使用。
- **Headline**（`headline`）：卡片、区块和图表标题。
- **Metric**（`metric`）：关键金额、比例和行情数字，使用 `tabular-nums`，不换行。
- **Body**（`body`）：页面说明、日报正文与状态解释。
- **Label**（`label`，`0.75rem` / `12px`）：表头、时间、来源、任务状态和辅助元数据；不承担长段阅读。

可见界面文字的字号下限为 `12px`。正文、按钮、标签、表格、图例、图表内文字和移动端说明都不得低于该下限；不能用缩小字号来挤入更多金融字段。

**The Numeric Integrity Rule.** 金额、比例、日期和表格数字保持列对齐；缺失值显示“—”，不得用 `0`、`0.00%` 或占位演示值替代。

## Layout

桌面采用固定侧栏与宽内容画布：侧栏宽 `214px`，主内容最大宽 `1680px`，页面内边距为上 `1.8rem`、左右 `2.1rem`、下 `3rem`。页面由短标题、状态条、并列信息块、指标卡、图表和表格构成；密度较高，但每层只回答一种问题。

间距使用统一的 4px 基础尺度：同一卡片内部相关内容采用 `0.75rem`，并列卡片与列之间采用 `1rem`，页面主要模块行之间采用 `1.25rem`，卡片内边距统一为 `1rem`。组件自身不再叠加额外外边距，避免有的模块黏连、有的模块出现双倍空白。

### 信息架构

- 一级导航固定为“今日、标的、组合、问 Hermes”四项；ADR-011 已取代旧“今日、观察池、持仓、回顾、问 Hermes”导航。默认始终显示最新数据，历史日期只在标的和组合页面局部开启。
- “今日”首屏顺序固定为：系统与数据新鲜度 → 关注事项 → 结构化日报 → 观察池与组合概览。关注事项和日报在宽屏并列，在窄屏按该顺序堆叠。
- 标的详情以“总览、行情、估值（ETF 显示为 ETF 指标）、研究、事件、来源”六个页签渐进展开。
- 组合页将“当前持仓、手工记账、流水、交易建议”分区；人工写入不得伪装成分析区的自动动作。
- 来源、日报原文和运行细节默认折叠或放入次级页签，不占据首屏，但始终可追溯。

### Responsive

- `>1100px`：固定 `214px` 侧栏与宽内容区，装饰性指标图标可见。
- `≤1100px`：主内容左右内边距收至 `1rem`，指标卡降至至少 `120px`，装饰性指标图标隐藏。
- `≤760px`：Streamlit 侧栏成为可收起的导航抽屉，展开宽 `230px`；可恢复入口明确标注“导航”，触控尺寸至少 `86 × 44px`。主内容顶部内边距为 `1rem`，标题为 `1.4rem`，状态条改为顶部对齐，指标卡至少 `110px`。
- 多列区域在窄屏按 DOM 顺序变成单列。观察池桌面表格展示“代码、名称、最新价、日涨跌、跟踪指数、行情日”六列；`≤760px` 时只保留“代码、名称、最新价、日涨跌”四个核心列，跟踪指数与行情日在下方标的详情中查看。

**The First Viewport Rule.** 任何新增首屏模块都不得越过新鲜度、关注事项与日报；用户必须先知道数据是否可信，再看到投资结果。

## Elevation & Depth

系统以表面色差和 `1px` 描边建立结构。亮色卡片只使用极轻的环境阴影（`0 1px 2px rgba(16, 24, 40, 0.03)`）；暗色模式不使用阴影。没有玻璃拟态、强投影、浮空岛或渐变背景。

**The Flat-by-Default Rule.** 卡片静止时应像工作纸张上的清晰分区；层级来自边框、留白和标题，不来自悬浮感。

## Shapes

形态克制且略带圆角：主要卡片使用 `surface`，状态容器与数据框使用 `container`，按钮、导航项和新鲜度条使用 `control`，严重度标签使用 `badge`。状态点是唯一常规圆形元素；品牌标记和线性指标图标保持简单几何轮廓。

空状态使用虚线边框与柔和表面色；普通卡片使用实线边框。不要引入胶囊化大圆角，也不要为同类卡片创建新的半径。

## Components

### Navigation

- 侧栏条目高 `42px`，默认使用次级文本色；悬停只提升表面与文字对比，过渡为 `120ms ease`。
- 当前项使用操作蓝底、白字和较高字重。四项可访问名称必须是纯中文：“今日、标的、组合、问 Hermes”。
- 四个视觉图标使用同一套 authored 线性 SVG，通过 mask 伪元素绘制；图标只作装饰，不进入辅助技术名称，也不替代导航文字。
- 窄屏入口至少 `44px` 高并显示“导航”；关闭后仍可恢复抽屉，不允许只有含义不明的汉堡图标。

### 数据新鲜度与状态

- 新鲜度条是首屏最高优先级状态组件：白色或深色表面、细边框、语义色状态点和“OK / WARNING / STALE / FAILED”文字。
- WARNING、STALE 和 FAILED 原样显示；不得把降级状态弱化为普通提示，也不得静默回退到示例数据。
- 错误卡使用红色轻混合背景、虚线边框、用户可执行说明和错误码。空状态说明缺少什么、怎样形成真实数据，不使用虚假示例填充。

### Metric Cards

- 指标卡统一使用 `metric-card`：标题、主值、变化与解释四层；右侧 authored 线性 SVG 仅作辅助，窄屏隐藏。
- 正负值同时使用符号和高对比语义文字色；中性或未知值使用正文色和“—”。风险证据不足时必须写“证据不足 / 待评估”，不得显示成零风险。

### Cards、Charts 与 Tables

- 内容容器使用细描边、统一圆角和紧凑内边距。区块标题左侧为主问题，右侧只放数量、口径或时间等短元数据。
- 组合净值趋势图使用图形蓝线与低透明度面积；标的详情使用不复权日 K 与 MA5/MA20/MA30，均只在至少两条完整真实记录时绘制。日 K 遵循 A 股红涨绿跌，MA5、MA20、MA30 依次使用图形蓝、告警琥珀与指标紫。
- 环图只绘制大于零的真实分类；无数据时显示状态卡。多分类颜色只用于区分扇区，不替代图例。
- 表格保持紧凑、数字等宽、末行无多余分隔线；禁止以原始 JSON 作为界面。移动观察池不得用更小字号换取列数，固定保留四个核心行情列。

### 来源与追溯

- 结构化来源包括 TuShare、AkShare 的新浪/东方财富/同花顺适配器、Yahoo Finance、FRED、乐咕乐股，以及由新浪交易日历同步并可人工校准的本地交易日历。
- 标的详情“来源”页按数据域展示真实记录的 Provider、`provenance_id`、`as_of`/报告期与质量状态；空状态明确说明尚无来源记录，不生成替代来源。
- 来源是可展开的证据层，不抢占首屏；任何 fallback、过期或不可用状态必须在 freshness、quality 与 provenance 中显式保留。

### Hermes Conversation

- “问 Hermes”是 Dashboard 的第四个正式表面，采用持续会话、可恢复历史、顶部本机连接状态和底部问题输入；消息列表以细分隔线组织，不堆叠对话气泡。
- 历史会话放在连接状态下方的折叠管理区：先展示可扫描列表，再对单一选中会话提供打开、中文 Markdown 导出和删除；永久删除必须经过独立确认步骤。
- 空会话先说明能力边界，再给出系统内持仓/观察池与系统外股票的真实问题建议；系统内标的优先使用 Backend MCP，系统外标的必须标明公开来源、时点与事实缺口。
- 页面明确写出“不会连接券商、自动下单或修改 REAL 账本”。Hermes 可以研究、比较和解释，但不批准或执行交易。

### Forms 与真实操作

- 表单沿用 Streamlit 原生控件、`control` 圆角和至少 `36px` 高的按钮；主提交动作使用操作蓝，次要或拒绝动作保持描边。
- **所有持久写入都必须经过“填写或选择 → 可检查/可编辑预览 → 独立确认”三步。** 这条规则覆盖观察池增删、主观估值、研究笔记/观点/跟踪事项、期初迁入、现金和交易流水、REVERSAL、更改建议状态及登记外部实际成交；取消确认不得调用 Backend 写接口。
- REAL 组合的持仓迁入、现金变动、买入、卖出、分红、费用和流水更正均是用户手工账本操作。更正通过追加 REVERSAL 完成，不覆盖历史记录。
- Hermes 只创建建议。人负责批准或拒绝；批准后仍需手工登记实际日期、价格、数量和费用，界面只记账，不联系券商。

### Accessibility

- 保留浏览器与 Streamlit 的可见焦点样式和键盘顺序；不得用全局 CSS 移除焦点轮廓。
- 新鲜度和 Hermes 连接条使用 `role="status"`；状态必须包含文字，正负数必须包含符号，装饰 SVG 不得成为唯一标签。
- 小字号语义文字只使用前述深色 text token；明暗主题下新增颜色、图表文字和白字主按钮都必须重新核对对比度。
- 触控窄屏导航入口至少 `44px`；表格使用可滚动容器，正文不使用极低对比度的禁用态颜色；任何可见文字不得小于 `12px`。

## Do's and Don'ts

### Do

- **Do** 保持中文优先和系统主题；业务事实与计算只来自 Backend API，对话只通过本机 Hermes 会话网关。
- **Do** 按“状态/新鲜度 → 关注事项 → 日报 → 观察池/组合”组织首页，并保持来源细节渐进披露。
- **Do** 直出 WARNING、STALE、FAILED、质量标记、`as_of`、freshness、provenance 与引擎版本；需要时让用户继续展开证据。
- **Do** 把 REAL 持仓、现金和业务流水明确标成手工维护、仅追加、可审计的账本。
- **Do** 让每次持久写入停在预览层，只有独立确认后才写入 Backend。
- **Do** 将 `510300.SH`、`513650.SH`、`512890.SH` 视为产品默认观察池内容，但不把其价格、涨跌或风险写死为设计文案。

### Don't

- **Don't** 连接券商、自动下单、自动执行建议、同步实时券商持仓，或暗示 REAL 等于券商账户。
- **Don't** 让 Hermes 批准或执行交易；“已完成”只能表示用户在外部完成后手工登记实际结果。
- **Don't** 伪造缺失数据、静默降级、用演示数字填空，或把证据不足画成 `0%` 风险。
- **Don't** 把 PAPER 与 REAL 混合，把派生持仓当作可直接覆盖的权威状态，或删除历史流水来“更正”。
- **Don't** 用原始 JSON、过度圆角、强阴影、玻璃拟态、渐变和装饰动画破坏标准金融 Dashboard 的扫描效率。

### 验收基线

- 构图方向来自用户参考图与 `seed=c375a27a`；参考图只用于方向判断，不是用户逐像素批准稿或最终验收基准。最终以已经落地的真实工作流、业务边界和设计契约为准。
- 最终实现证据：`.impeccable/review/desktop.png` 与 `.impeccable/review/mobile.png`；桌面和窄屏均保持上述叙事顺序、状态可见性与可恢复导航。
- 设计契约以 `dashboard/app.py` 第一段正文 HTML 注释中的 `THESIS / OWN-WORLD / STORY / FIRST VIEWPORT / FORM / FINISH` 为准。
- 验收确认：无券商连接或自动执行；缺失与失败未被掩盖；人工账本与建议审批边界清晰；来源可追溯且默认不抢占首屏。
- 最新 Impeccable finish reviewer disposition 为 `ship`，未发现 material blocker。该结论记录的是已完成实现与最终截图，不是施工前预写的 `PASS` / `APPROVED`。

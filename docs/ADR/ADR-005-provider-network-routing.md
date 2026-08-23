# ADR-005：Provider 网络层分流策略（Per-Provider Proxy Routing）

> 状态：**Accepted（2026-08-23，M0.5 Spike 回流）**
>
> 关联：TS-05 Provider Architecture §5/§9、provider-capability-report R7、冻结规范 §12
>
> 类型：Spike 结论回流（冻结规范 §47："spike 报告是 ADR 的输入"）

---

## 1. 背景

M0.5 Spike 实测（2026-08-23）发现本机网络环境的三个事实：

1. 用户 Mac 常年运行代理（端口 7892，系统透明代理模式）；
2. **eastmoney push2his 直连被网络层拦截**（curl 直连 HTTP 000/0.28s；显式代理 HTTP 200/0.15s），而 TuShare / 新浪 / 乐咕 / 同花顺直连正常；
3. Yahoo Finance / FRED 依赖代理环境（当前经系统透明代理可达）。

## 2. 决策

### D1：Provider 网络配置进入配置层（provider-capability.yaml）

每个 provider 声明网络模式：

```yaml
providers:
  tushare:
    network:
      proxy: direct              # 国内直连
  akshare_sina:
    network:
      proxy: direct
  akshare_eastmoney:
    network:
      proxy: http://127.0.0.1:7892   # 本机实测：直连被阻，代理可通
  yahoo:
    network:
      proxy: env                 # 跟随系统/环境代理（透明代理环境）
  fred:
    network:
      proxy: env
```

取值：`direct`（强制直连，忽略环境代理）/ `http://host:port`（显式代理）/ `env`（跟随环境，默认）。

### D2：代理注入方式 = session.proxies 显式传入

- requests 环境变量代理（HTTPS_PROXY）对 AkShare 内部 session **实测未生效**；
- Provider 实现必须用 `requests.Session()` + `session.proxies` 显式注入（per-provider），不允许依赖环境变量隐式生效；
- 代理配置变化不改变 Provider 接口契约，只改 yaml。

### D3：A 股日线 fallback 从 eastmoney 切换为新浪源

- AkShare `stock_zh_a_daily`（新浪源）实测可用（含 qfq 前复权）；
- fallback 链：`tushare(daily) → akshare_sina(stock_zh_a_daily) → DATA_UNAVAILABLE`；
- eastmoney 源保留为 ETF 行情 fallback（`fund_etf_hist_em`，配代理）。

### D4：no-proxy 语义

- 国内源（tushare/新浪/乐咕/同花顺）一律 `direct`，**禁止**经代理绕行（避免不必要的链路与超时）；
- 国外源（yahoo/fred）`env`（当前透明代理环境无需显式配置）。

## 3. 影响

- TS-05 provider 元数据 schema 增加 `network.proxy` 字段（capability matrix 同步）；
- M1 Provider 实现的网络层必须支持三态（direct/proxy/env）+ 超时/重试沿用 TS-05 契约；
- 代理故障降级：eastmoney 代理不可用时该源标记 UNAVAILABLE（禁止静默 fallback，记录 quality_flags）；
- 不修改 TS-01~TS-04 任何冻结项。

## 4. 关联

- provider-capability-report.md R7（本 ADR 的输入）
- ADR-004（远程化路线：Tailscale 不改变本机出站代理语义）

"""Hermes Investment Office — 全局配置（Settings）。

依据：
- TS-03（配置体系约定）
- ADR-004 D5（配置化预留：bind_host / base_url / auth.enabled）
- 冻结规范 §33.3（认证边界：v0.1 localhost 无 token，远程化时激活）

规则（冻结）：
- 所有 token / key 只走环境变量（TS-05 §7），不进代码、不进 git
- 禁止在业务逻辑中硬编码 127.0.0.1 为唯一合法形态（ADR-004 D5）
- 具体模型名禁止出现在业务配置（Model Routing 在 Hermes 侧，冻结规范 §34）
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend 全局配置。环境变量前缀 HERMES_，如 HERMES_DB_URL。"""

    model_config = SettingsConfigDict(
        env_prefix="HERMES_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 服务绑定（ADR-004 D5：v0.1 默认本地单机，远程化时改 bind_host）----
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    base_url: str = "http://127.0.0.1:8000"

    # ---- 认证预留（ADR-004 D3 / 冻结规范 §33.3：v0.1 关闭）----
    auth_enabled: bool = False
    auth_token_env: str = "HERMES_BACKEND_TOKEN"

    # Backend scheduler is opt-in; the default is safe for an empty local DB.
    scheduler_enabled: bool = False
    scheduler_timezone: str = "Asia/Shanghai"
    scheduler_hour: int = 18
    scheduler_minute: int = 0

    # ---- 数据库 ----
    # Credentials must come from HERMES_DB_URL or the ignored local .env.
    # The fallback intentionally contains no password.
    db_url: str = "postgresql+psycopg2://127.0.0.1:5432/hermes"

    # ---- 日志 ----
    log_level: str = "INFO"
    log_dir: str = "logs"

    # ---- 外部 Provider（token 走独立环境变量，见 providers 各包）----
    tushare_token: str = ""
    fred_api_key: str = ""

    # ---- 配置与数据目录（TS-05 §3.5/§7；相对 backend/ 运行目录）----
    config_dir: str = "config"
    data_dir: str = "../data"          # data/raw、data/parquet、data/documents

    # ---- 时区（冻结规范 §14.2：UTC 存储 + 时区标注）----
    default_tz: str = "Asia/Shanghai"

    @property
    def provider_capability_path(self) -> str:
        """provider-capability.yaml 权威源（TS-05 §4；ADR-005 D1）。"""
        return f"{self.config_dir}/provider-capability.yaml"

    @property
    def providers_runtime_path(self) -> str:
        """providers.yaml 运行参数（TS-05 §3.5）。"""
        return f"{self.config_dir}/providers.yaml"

    @property
    def etf_valuation_band_path(self) -> str:
        """ETF Engine 估值带阈值配置（TS-06 §4.6）。"""
        return f"{self.config_dir}/etf-valuation-band.yaml"

    @property
    def qdii_alignment_path(self) -> str:
        """QDII 四日期交易日对齐阈值配置。"""
        return f"{self.config_dir}/qdii-alignment.yaml"

    @property
    def freshness_config_path(self) -> str:
        """Daily Context 字段级 freshness 阈值（TS-04 §7）。"""
        return f"{self.config_dir}/freshness.yaml"

    @property
    def attention_rules_path(self) -> str:
        """确定性 Attention 规则的唯一配置源（TS-04 §8）。"""
        return f"{self.config_dir}/attention_rules.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

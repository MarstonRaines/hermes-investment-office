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

    # ---- 数据库 ----
    db_url: str = "postgresql+psycopg2://hermes:hermes@127.0.0.1:5432/hermes"

    # ---- 日志 ----
    log_level: str = "INFO"
    log_dir: str = "logs"

    # ---- 外部 Provider（token 走独立环境变量，见 providers 各包）----
    tushare_token: str = ""
    fred_api_key: str = ""

    # ---- 时区（冻结规范 §14.2：UTC 存储 + 时区标注）----
    default_tz: str = "Asia/Shanghai"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

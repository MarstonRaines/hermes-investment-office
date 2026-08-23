# =====================================================================
# backend/app/providers/runtime_config.py —— providers.yaml 运行参数加载（TS-05 §3.5）
# =====================================================================
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

__all__ = [
    "RuntimeRateLimit",
    "RuntimeProviderConfig",
    "RuntimeProviderConfigs",
    "load_runtime_configs",
]


class RuntimeRateLimit(BaseModel):
    qps: float = 1.0
    burst: int = 3
    daily_quota: int = 3000


class RuntimeProviderConfig(BaseModel):
    token_env: str | None = None       # 环境变量名（token 不进文件）
    score: int | None = None           # TuShare 积分档位
    timeout_seconds: float = 20.0
    max_retries: int = 2
    retry_backoff_base: float = 1.0
    rate_limit: RuntimeRateLimit = Field(default_factory=RuntimeRateLimit)


class RuntimeProviderConfigs(BaseModel):
    version: str = "0.1"
    providers: dict[str, RuntimeProviderConfig]

    @field_validator("version", mode="before")
    @classmethod
    def _coerce_version(cls, v: object) -> str:
        """YAML `version: 0.1` 解析为 float，统一转 str。"""
        return str(v) if v is not None else ""

    def get(self, name: str) -> RuntimeProviderConfig | None:
        return self.providers.get(name)


def load_runtime_configs(path: str | Path) -> RuntimeProviderConfigs:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"providers.yaml 不存在: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    return RuntimeProviderConfigs.model_validate(raw)

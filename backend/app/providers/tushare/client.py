# =====================================================================
# backend/app/providers/tushare/client.py —— TuShare SDK 封装
#
# - token 从配置注入（禁止进代码/git）；
# - 同步 SDK 调用经 asyncio.to_thread 执行，不阻塞事件循环；
# - 错误映射（TS-05 §2.0.1）：限流/积分 → ProviderRateLimited，
#   token/权限 → ProviderAuthError，网络 → ProviderTimeout/Unavailable。
# =====================================================================
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:  # tushare 为可选依赖（providers.yaml 未配置 token 时仍可导入）
    import tushare as ts
except ImportError:  # pragma: no cover
    ts = None  # type: ignore[assignment]


class TushareClientError(Exception):
    """SDK 调用失败（错误映射前的中转）。"""


class TushareClient:
    """TuShare Pro API 客户端（token 注入 + typed 错误映射）。"""

    def __init__(self, token: str, timeout: float = 20.0) -> None:
        if not token:
            raise ValueError("tushare token 未配置")
        if ts is None:
            raise RuntimeError("tushare 未安装")
        self._pro = ts.pro_api(token)
        self._timeout = timeout

    async def call(self, api_name: str, **params: Any):
        """调用 pro.<api>(**params)，返回 DataFrame。"""
        return await asyncio.to_thread(self._call_sync, api_name, **params)

    def _call_sync(self, api_name: str, **params: Any):
        try:
            fn = getattr(self._pro, api_name)
            return fn(**params, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 —— tushare SDK 异常信息携带语义
            raise map_tushare_error(exc, api_name) from exc


def map_tushare_error(exc: Exception, api_name: str) -> Exception:
    """把 tushare SDK 异常映射为 typed ProviderError（按错误文案启发式分类）。"""
    from app.providers.contracts.base import (
        ProviderAuthError,
        ProviderDataError,
        ProviderRateLimited,
        ProviderTimeout,
        ProviderUnavailable,
    )

    msg = str(exc)
    low = msg.lower()
    if any(k in msg for k in ("每分钟最多访问", "访问频率", "frequence", "超过", "限流", "quota")):
        return ProviderRateLimited(f"{api_name}: {msg}")
    if any(k in msg for k in ("积分", "权限", "token", "登录", "permission", "auth", "wrong")):
        return ProviderAuthError(f"{api_name}: {msg}")
    if any(k in low for k in ("timeout", "timed out", "读超时", "连接超时")):
        return ProviderTimeout(f"{api_name}: {msg}")
    if any(k in low for k in ("network", "connection", "无法连接", "requests.exceptions")):
        return ProviderUnavailable(f"{api_name}: {msg}")
    # 接口本身成功但返回内容异常（列缺失等）在调用方校验，这里按数据错误兜底
    return ProviderDataError(f"{api_name}: {msg}")

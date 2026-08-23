# =====================================================================
# backend/app/providers/http.py —— Provider HTTP 会话（ADR-005 D2，冻结）
#
# ADR-005 D2：Provider 实现必须用 requests.Session() + session.proxies 显式
# 注入（per-provider），不允许依赖环境变量隐式生效。
#
# 三态（ADR-005 D1）：
#   direct          —— trust_env=False：强制直连，忽略环境代理（国内源）
#   env             —— trust_env=True：跟随环境/系统代理（Yahoo/FRED）
#   http(s)://host  —— proxies 显式注入 + trust_env=False（eastmoney 类）
# =====================================================================
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import requests

from app.providers.contracts.base import (
    ProviderDataError,
    ProviderTimeout,
    ProviderUnavailable,
)

__all__ = [
    "make_session",
    "http_get_text",
    "http_get_json",
    "map_http_error",
]


def make_session(proxy_mode: str) -> requests.Session:
    """按 ADR-005 三态构建 requests.Session（显式注入，不依赖隐式环境行为）。"""
    s = requests.Session()
    if proxy_mode == "direct":
        s.trust_env = False
    elif proxy_mode == "env":
        s.trust_env = True                       # requests 默认读取 HTTP(S)_PROXY
    elif proxy_mode.startswith(("http://", "https://")):
        s.trust_env = False
        s.proxies = {"http": proxy_mode, "https": proxy_mode}
    else:
        raise ValueError(f"invalid proxy mode: {proxy_mode!r}")
    return s


async def http_get_text(
    session: requests.Session,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 20.0,
    raise_for_status: bool = True,
) -> str:
    """异步 GET 返回文本。网络/超时/HTTP 错误映射为 typed ProviderError。"""
    try:
        resp = await asyncio.to_thread(
            session.get, url, params=params, headers=headers, timeout=timeout
        )
    except requests.Timeout as exc:
        raise ProviderTimeout(f"GET {url} timeout") from exc
    except requests.ConnectionError as exc:
        raise ProviderUnavailable(f"GET {url} connection failed") from exc
    except requests.RequestException as exc:
        raise ProviderUnavailable(f"GET {url} failed: {exc}") from exc
    if raise_for_status:
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                from app.providers.contracts.base import ProviderRateLimited

                retry_after = exc.response.headers.get("Retry-After")
                raise ProviderRateLimited(
                    f"GET {url} rate limited (429)",
                    retry_after=float(retry_after) if retry_after else None,
                ) from exc
            if exc.response is not None and exc.response.status_code in (401, 403):
                from app.providers.contracts.base import ProviderAuthError

                raise ProviderAuthError(f"GET {url} auth failed: {exc.response.status_code}") from exc
            raise ProviderUnavailable(f"GET {url} http {exc.response.status_code if exc.response else '?'}") from exc
    return resp.text


async def http_get_json(
    session: requests.Session,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 20.0,
) -> Any:
    """异步 GET 返回 JSON。非 JSON 响应 → ProviderDataError。"""
    text = await http_get_text(session, url, params=params, headers=headers, timeout=timeout)
    import json

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderDataError(f"GET {url}: invalid JSON response") from exc


def map_http_error(exc: Exception, url: str) -> Exception:
    """把任意异常映射为 Provider typed 错误（内部兜底，正常路径不触发）。"""
    if isinstance(exc, requests.Timeout):
        return ProviderTimeout(f"GET {url} timeout")
    if isinstance(exc, requests.ConnectionError):
        return ProviderUnavailable(f"GET {url} connection failed")
    if isinstance(exc, requests.RequestException):
        return ProviderUnavailable(f"GET {url} failed: {exc}")
    return exc

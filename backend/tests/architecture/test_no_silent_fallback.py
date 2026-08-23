# =====================================================================
# tests/architecture/test_no_silent_fallback.py —— ARCH-DSC-002 静态扫描
#
# TS-05 §5.3 禁止 silent fallback 的架构强制点（架构测试版）：
# 1. providers 包内部禁止跨 provider 取数（换源只存在于 Data Gateway）；
# 2. 数据服务（market_data/fundamentals/fx/calendar/corporate_actions）不得
#    import 具体 provider 实现（只能依赖 contracts / gateway / factory）。
# =====================================================================
from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"

# 编排层例外：只有 gateway/factory/bootstrap 允许触碰具体实现
ORCHESTRATION_MODULES = {
    "app.providers.gateway",
    "app.providers.factory",
    "app.providers.bootstrap",
    "app.providers.registry",
}

DATA_SERVICES = [
    "app.market_data",
    "app.fundamentals",
    "app.fx",
    "app.calendar",
    "app.corporate_actions",
    "app.etf",
]

CONCRETE_PROVIDER_PREFIXES = (
    "app.providers.tushare",
    "app.providers.akshare",
    "app.providers.yahoo",
    "app.providers.fred",
    "app.providers.legulegu",
)


def _imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def test_providers_never_import_each_other() -> None:
    """TS-05 §5.3 强制点 1：Provider 实现内部禁止调用其他 provider（换源只发生在 gateway）。"""
    for pkg in ("tushare", "akshare", "yahoo", "fred", "legulegu"):
        pkg_dir = APP_DIR / "providers" / pkg
        for py in pkg_dir.rglob("*.py"):
            if py.name == "__init__.py":
                continue
            for imp in _imports_of(py):
                if imp.startswith(CONCRETE_PROVIDER_PREFIXES) and not imp.startswith(
                    f"app.providers.{pkg}"
                ):
                    raise AssertionError(
                        f"{py.relative_to(APP_DIR)}: 禁止跨 provider import {imp}"
                    )


def test_data_services_never_import_concrete_providers() -> None:
    """TS-05 §5.3 强制点 2：数据服务必须经由 gateway，禁止直连 provider 实现。"""
    for service in DATA_SERVICES:
        svc_dir = APP_DIR / service.replace("app.", "")
        for py in svc_dir.rglob("*.py"):
            for imp in _imports_of(py):
                if imp.startswith(CONCRETE_PROVIDER_PREFIXES):
                    raise AssertionError(
                        f"{py.relative_to(APP_DIR)}: 数据服务禁止 import 具体 provider {imp}（走 gateway）"
                    )


def test_no_provider_client_in_api_or_mcp() -> None:
    """TS-05 §8.1：api/ 与 mcp/ 禁止 import providers.*（Hermes 永不触达 provider）。"""
    for layer in ("api", "mcp"):
        layer_dir = APP_DIR / layer
        if not layer_dir.exists():
            continue
        for py in layer_dir.rglob("*.py"):
            for imp in _imports_of(py):
                if imp.startswith("app.providers") and imp not in ORCHESTRATION_MODULES:
                    raise AssertionError(
                        f"{py.relative_to(APP_DIR)}: {layer} 层禁止 import {imp}"
                    )

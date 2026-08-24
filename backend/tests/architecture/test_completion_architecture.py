"""Executable M5/M7 boundary checks from TS-08 ARCH-DEP/ARCH-DSH."""

from __future__ import annotations

import re
from pathlib import Path

from app.mcp.server import MCP_ALLOWED_TOOLS, MCP_TOOL_PERMISSIONS

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
DASHBOARD = ROOT.parent / "dashboard"
SKILLS = ROOT.parent / "skills"


def _imports(path: Path) -> list[str]:
    return re.findall(r"^(?:from|import)\s+([\w.]+)", path.read_text(), re.M)


def test_api_and_mcp_do_not_bypass_service_facades():
    for root in (APP / "api", APP / "mcp"):
        for path in root.rglob("*.py"):
            imports = _imports(path)
            assert not any(
                module.startswith("app.models")
                or module.startswith("app.") and module.endswith(".models")
                or module.startswith("app.") and ".models." in module
                or module.startswith("app.") and ".repository" in module
                or module.startswith("app.") and ".engine" in module
                for module in imports
            ), f"{path.relative_to(APP)} 绕过 service facade: {imports}"


def test_dashboard_is_api_only():
    dependency_text = "\n".join(
        path.read_text() for path in (DASHBOARD / "requirements.txt", DASHBOARD / "app.py")
    ).lower()
    for forbidden in ("sqlalchemy", "psycopg", "duckdb", "pandas", "connect("):
        assert forbidden not in dependency_text
    assert "urlopen" in dependency_text
    assert "127.0.0.1" in dependency_text
    assert "market_value" not in dependency_text
    assert "nav_cny" not in dependency_text


def test_runtime_skills_are_present_and_guarded():
    required = {
        "investment-runtime-policy", "investment-policy", "daily-brief", "stock-research",
        "etf-analysis", "valuation-analysis", "portfolio-review", "thesis-review",
    }
    assert {path.name for path in SKILLS.iterdir() if path.is_dir()} >= required
    runtime = (SKILLS / "investment-runtime-policy" / "SKILL.md").read_text()
    for phrase in ("provenance", "freshness", "ACCOUNT_WRITE", "REAL", "MCP"):
        assert phrase in runtime


def test_mcp_permissions_never_expose_account_write():
    assert set(MCP_TOOL_PERMISSIONS) == MCP_ALLOWED_TOOLS
    assert "ACCOUNT_WRITE" not in set(MCP_TOOL_PERMISSIONS.values())
    assert "portfolio_transactions" not in MCP_ALLOWED_TOOLS

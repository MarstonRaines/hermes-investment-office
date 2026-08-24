# backend/app/mcp/__init__.py —— MCP 层（TS-07）
from app.mcp.server import (
    ADR006_MCP_TOOLS,
    FROZEN_MCP_TOOLS,
    M1_5_MCP_TOOLS,
    MCP_ALLOWED_TOOLS,
    MCPDomainError,
    build_mcp_server,
    envelope,
)

__all__ = [
    "ADR006_MCP_TOOLS",
    "FROZEN_MCP_TOOLS",
    "M1_5_MCP_TOOLS",
    "MCP_ALLOWED_TOOLS",
    "MCPDomainError",
    "build_mcp_server",
    "envelope",
]

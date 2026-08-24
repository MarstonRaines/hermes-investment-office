"""Deterministic risk calculations; no database or provider dependencies."""

from app.risk.engine import ENGINE_VERSION, compute_risk

__all__ = ["ENGINE_VERSION", "compute_risk"]

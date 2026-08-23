# =====================================================================
# backend/app/providers/akshare/__init__.py —— AkShare provider 包
# =====================================================================
from app.providers.akshare.etf import AkShareEastmoneyEtfProvider
from app.providers.akshare.fundamentals import AkShareThsFundamentalProvider
from app.providers.akshare.market import AkShareSinaMarketProvider

__all__ = [
    "AkShareEastmoneyEtfProvider",
    "AkShareSinaMarketProvider",
    "AkShareThsFundamentalProvider",
]

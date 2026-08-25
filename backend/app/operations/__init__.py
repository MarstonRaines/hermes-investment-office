"""本地产品初始化与运维入口。"""

from app.operations.bootstrap import ensure_product_defaults

__all__ = ["ensure_product_defaults"]

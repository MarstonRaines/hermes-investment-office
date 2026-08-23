"""基础日志配置（冻结规范 §35.4：Job 失败必须记录，不可静默吞掉）。

约定：
- 统一 logging 配置，根 logger 名 app；
- 结构化字段：timestamp / level / logger / message；
- 日志目录可配置（settings.log_dir），默认 logs/。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.common.config import settings


def setup_logging() -> None:
    root = logging.getLogger("app")
    if root.handlers:  # 幂等
        return

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    root.setLevel(settings.log_level.upper())

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    log_dir = Path(settings.log_dir)
    if settings.log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "backend.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

    # 第三方库降噪（保留 SQLAlchemy WARN）
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

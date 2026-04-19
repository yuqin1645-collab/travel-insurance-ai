from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("ai_review")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False  # 不向 root logger 传播，防止日志重复输出

    class _SafeExtraFormatter(logging.Formatter):
        """确保日志记录即使缺少 forceid/stage/attempt 也不会 KeyError。"""

        def format(self, record: logging.LogRecord) -> str:
            if not hasattr(record, "forceid"):
                setattr(record, "forceid", "-")
            if not hasattr(record, "stage"):
                setattr(record, "stage", "-")
            if not hasattr(record, "attempt"):
                setattr(record, "attempt", 0)
            return super().format(record)

    fmt = _SafeExtraFormatter(
        "%(asctime)s %(levelname)s forceid=%(forceid)s stage=%(stage)s attempt=%(attempt)s %(message)s"
    )

    stream = logging.StreamHandler()
    try:
        stream.setEncoding("utf-8")
    except Exception:
        pass
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    try:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        from logging.handlers import TimedRotatingFileHandler
        fh = TimedRotatingFileHandler(
            log_dir / "ai_review.log",
            when="midnight", interval=1, backupCount=30, encoding="utf-8"
        )
        fh.suffix = "%Y%m%d"
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        # 日志文件不可写时，至少保证控制台输出不受影响
        pass

    return logger


LOGGER = setup_logger()


def log_extra(forceid: str = "-", stage: str = "-", attempt: int = 0) -> Dict[str, object]:
    return {"forceid": forceid or "-", "stage": stage or "-", "attempt": attempt}


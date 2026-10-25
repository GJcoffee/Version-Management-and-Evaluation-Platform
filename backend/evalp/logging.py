"""日志（详设 16.3）：structlog JSON，写入 {data_dir}/logs/evalp-YYYYMMDD.log 并输出到控制台，按天保留 14 天。

上下文字段（request_id、session_id、turn_id、run_id、job_id）通过 structlog.contextvars 绑定：
``structlog.contextvars.bind_contextvars(session_id=...)``。
"""

from __future__ import annotations

import logging
import re
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

LOGGER_NAME = "evalp"
LOG_RETENTION_DAYS = 14
_LOG_FILE_RE = re.compile(r"^evalp-(\d{8})\.log$")

_shared_processors: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]

structlog.configure(
    processors=[*_shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=False,
)


class DailyFileHandler(logging.Handler):
    """按天写入 evalp-YYYYMMDD.log；跨天时切换文件并清理超过保留天数的旧日志。"""

    def __init__(self, logs_dir: Path, retention_days: int = LOG_RETENTION_DAYS) -> None:
        super().__init__()
        self.logs_dir = logs_dir
        self.retention_days = retention_days
        self._day: str | None = None
        self._stream: Any = None
        self._io_lock = threading.Lock()

    def _today(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%d")

    def _open(self, day: str) -> None:
        if self._stream is not None:
            self._stream.close()
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._stream = (self.logs_dir / f"evalp-{day}.log").open("a", encoding="utf-8")
        self._day = day
        self.cleanup(day)

    def cleanup(self, today: str | None = None) -> None:
        """删除早于保留期的日志文件。"""
        today_dt = datetime.strptime(today or self._today(), "%Y%m%d").replace(tzinfo=UTC)
        limit = (today_dt - timedelta(days=self.retention_days)).strftime("%Y%m%d")
        for p in self.logs_dir.glob("evalp-*.log"):
            m = _LOG_FILE_RE.match(p.name)
            if m and m.group(1) < limit:
                try:
                    p.unlink()
                except OSError:
                    pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._io_lock:
                day = self._today()
                if self._stream is None or day != self._day:
                    self._open(day)
                self._stream.write(msg + "\n")
                self._stream.flush()
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def close(self) -> None:
        with self._io_lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        super().close()


def _formatter(renderer: Any) -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=_shared_processors,
    )


def setup_logging(logs_dir: Path, *, console: bool = True, level: int = logging.INFO) -> None:
    """配置 evalp 日志：JSON 文件 + 控制台。可重复调用（会替换旧的 handler）。"""
    shutdown_logging()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    file_handler = DailyFileHandler(logs_dir)
    file_handler.setFormatter(_formatter(structlog.processors.JSONRenderer(ensure_ascii=False)))
    file_handler._evalp = True  # type: ignore[attr-defined]
    logger.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(_formatter(structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())))
        console_handler._evalp = True  # type: ignore[attr-defined]
        logger.addHandler(console_handler)


def shutdown_logging() -> None:
    """移除并关闭 setup_logging 安装的 handler。"""
    logger = logging.getLogger(LOGGER_NAME)
    for h in list(logger.handlers):
        if getattr(h, "_evalp", False):
            logger.removeHandler(h)
            h.close()


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取 structlog 日志器；name 通常传 ``__name__``（以 evalp. 开头）。"""
    return structlog.get_logger(name or LOGGER_NAME)

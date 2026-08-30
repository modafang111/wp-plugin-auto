"""File logger that never writes secrets."""

from __future__ import annotations

import logging
import re
import sys
import traceback
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_-]{8,})"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I),
    re.compile(r"(password\s*[=:]\s*)\S+", re.I),
    re.compile(r"(api[_-]?key\s*[=:]\s*)\S+", re.I),
    re.compile(r"(secret\s*[=:]\s*)\S+", re.I),
    re.compile(r"(token\s*[=:]\s*)\S+", re.I),
]


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        self.secrets = [s for s in (secrets or []) if s and len(s) >= 4]

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        for pattern in SECRET_PATTERNS:
            text = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", text)
        return text


def setup_logger(
    logs_dir: Path,
    *,
    slug: str | None = None,
    secrets: list[str] | None = None,
) -> tuple[logging.Logger, Path]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{slug or 'run'}.log"
    log_path = logs_dir / name
    logger = logging.getLogger("base-wp-ja-auto")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    formatter = RedactingFormatter(secrets)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger, log_path


def log_exception(logger: logging.Logger, prefix: str = "例外") -> None:
    logger.error("%s\n%s", prefix, traceback.format_exc())

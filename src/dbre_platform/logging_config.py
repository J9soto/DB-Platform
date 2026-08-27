"""Structured (JSON) logging setup.

The platform intentionally does not depend on a third-party logging library
(e.g. ``structlog``). A JSON formatter over the standard library's
``logging`` module gets us machine-parseable logs -- which is what actually
matters for shipping to CloudWatch Logs, Loki, or any other log sink -- with
one dependency-free module. See docs/decisions/0004-stdlib-logging.md.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

_RESERVED_LOG_RECORD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName",
}


class JSONFormatter(logging.Formatter):
    """Renders each log record as a single line of JSON.

    Any extra keyword arguments passed via ``logger.info(msg, extra={...})``
    are merged into the top-level JSON object, so structured context (e.g.
    ``request_id``, ``environment``) survives log aggregation.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure the root ``dbre_platform`` logger.

    Parameters
    ----------
    level:
        Standard logging level name (``DEBUG``, ``INFO``, ``WARNING``, ...).
    json_output:
        When ``True`` (the default, and what CI/production should use), logs
        are emitted as JSON. When ``False`` (handy for interactive local CLI
        use), a short human-readable format is used instead.
    """
    root = logging.getLogger("dbre_platform")
    root.setLevel(level.upper())
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stderr)
    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"dbre_platform.{name}")

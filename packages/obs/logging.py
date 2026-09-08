"""Structured logging (T-7.5).

One JSON object per line on stderr: ``ts`` (ISO-8601 UTC), ``level``,
``logger``, ``msg``, plus any keyword passed via ``extra=``.  No
dependency -- stdlib ``json`` -- and a ``text`` mode for local work.

    from packages.obs.logging import configure_logging
    configure_logging(level="INFO", fmt="json")
    log.info("admitted", extra={"session": sid, "tenant": tenant})
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any, Literal

Format = Literal["json", "text"]

# LogRecord attributes that are structural, not payload -- everything else
# on the record dict was put there by an ``extra=`` and belongs in the line.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _dt.datetime.fromtimestamp(record.created, tz=_dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(*, level: str = "INFO", fmt: Format = "json") -> None:
    """Replace the root handler with a single stderr handler in ``fmt``.

    Idempotent: safe to call more than once (e.g. from tests)."""
    handler = logging.StreamHandler()
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())

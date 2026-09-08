"""T-7.5 -- structured logging."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest

from packages.obs.logging import JsonFormatter, configure_logging


@pytest.fixture
def _restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def _emit(**extra: object) -> dict[str, object]:
    rec = logging.LogRecord(
        "ringfence.test", logging.INFO, __file__, 1, "hello %s", ("world",), None
    )
    for k, v in extra.items():
        setattr(rec, k, v)
    return json.loads(JsonFormatter().format(rec))  # type: ignore[no-any-return]


def test_line_is_json_with_the_core_fields() -> None:
    obj = _emit()
    assert obj["level"] == "INFO"
    assert obj["logger"] == "ringfence.test"
    assert obj["msg"] == "hello world"  # %-args are rendered
    assert "T" in obj["ts"] and obj["ts"].endswith("+00:00")


def test_extras_are_merged_reserved_fields_are_not() -> None:
    obj = _emit(session="s1", tenant="acme", status=200)
    assert obj["session"] == "s1" and obj["tenant"] == "acme" and obj["status"] == 200
    assert "pathname" not in obj and "processName" not in obj


def test_exception_is_captured() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        rec = logging.LogRecord(
            "ringfence.test", logging.ERROR, __file__, 1, "failed", (), __import__("sys").exc_info()
        )
    obj = json.loads(JsonFormatter().format(rec))
    assert "ValueError: boom" in obj["exc"]


def test_configure_logging_is_idempotent_and_swappable(_restore_root_logger: None) -> None:
    configure_logging(level="DEBUG", fmt="json")
    configure_logging(level="INFO", fmt="text")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert root.level == logging.INFO

    buf = io.StringIO()
    root.handlers[0].stream = buf  # type: ignore[attr-defined]
    logging.getLogger("ringfence.x").warning("plain-line")
    assert "plain-line" in buf.getvalue() and not buf.getvalue().lstrip().startswith("{")

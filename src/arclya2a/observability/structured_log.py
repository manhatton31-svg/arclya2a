"""Structured JSON logging for operational events."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from arclya2a.settings import get_settings


class JsonLogFormatter(logging.Formatter):
    """Emit one JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = record.event
        if hasattr(record, "fields") and isinstance(record.fields, dict):
            payload.update(record.fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def json_logs_enabled() -> bool:
    return get_settings().json_logs


def configure_logging(*, level: int = logging.INFO) -> None:
    """Configure root logging with JSON or human-readable format."""
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    if json_logs_enabled():
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)


def log_event(logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Log a structured operational event."""
    if json_logs_enabled():
        record = logger.makeRecord(
            logger.name,
            level,
            "(structured)",
            0,
            event,
            (),
            None,
        )
        record.event = event  # type: ignore[attr-defined]
        record.fields = fields  # type: ignore[attr-defined]
        logger.handle(record)
        return

    parts = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.log(level, "%s %s", event, parts)
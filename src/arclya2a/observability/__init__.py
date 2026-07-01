"""Production observability: structured logging, ops events, and status."""

from arclya2a.observability.dashboard import build_ops_dashboard
from arclya2a.observability.ops_events import record_ops_event
from arclya2a.observability.ops_status import build_ops_status
from arclya2a.observability.security_events import (
    build_security_metrics,
    list_security_events,
    record_security_event,
)
from arclya2a.observability.structured_log import configure_logging, log_event

__all__ = [
    "build_ops_dashboard",
    "build_ops_status",
    "build_security_metrics",
    "configure_logging",
    "list_security_events",
    "log_event",
    "record_ops_event",
    "record_security_event",
]
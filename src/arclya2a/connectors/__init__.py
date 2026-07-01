"""External service connectors for agent tool execution."""

from arclya2a.connectors.base import ConnectorResult
from arclya2a.connectors.gmail import GmailConnector
from arclya2a.connectors.google_calendar import GoogleCalendarConnector
from arclya2a.connectors.linear import LinearConnector
from arclya2a.connectors.notion import NotionConnector

CONNECTORS = {
    "gmail": GmailConnector,
    "google_calendar": GoogleCalendarConnector,
    "linear": LinearConnector,
    "notion": NotionConnector,
}

__all__ = [
    "CONNECTORS",
    "ConnectorResult",
    "GmailConnector",
    "GoogleCalendarConnector",
    "LinearConnector",
    "NotionConnector",
]
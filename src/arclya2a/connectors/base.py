"""Base connector types and credential helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConnectorResult:
    success: bool
    tool_id: str
    connector: str
    action: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_code: str | None = None
    transient: bool = False
    skipped: bool = False
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "tool_id": self.tool_id,
            "connector": self.connector,
            "action": self.action,
            "data": self.data,
            "error": self.error,
            "error_code": self.error_code,
            "transient": self.transient,
            "skipped": self.skipped,
            "dry_run": self.dry_run,
        }


def env_present(*keys: str) -> bool:
    return all(os.environ.get(k, "").strip() for k in keys)


def env_any(*keys: str) -> str | None:
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def dry_run_enabled() -> bool:
    try:
        from arclya2a.partners.sandbox import is_sandbox_active, sandbox_tools_dry_run_default

        if is_sandbox_active() and sandbox_tools_dry_run_default():
            return True
    except ImportError:
        pass
    from arclya2a.settings import get_settings

    return get_settings().tool_dry_run


class BaseConnector:
    name: str = "base"

    def is_available(self, tool_def: dict[str, Any]) -> bool:
        required = tool_def.get("required_env", [])
        if not env_present(*required):
            return False
        return True

    def execute(
        self,
        *,
        tool_id: str,
        action: str,
        params: dict[str, Any],
        tool_def: dict[str, Any],
    ) -> ConnectorResult:
        raise NotImplementedError
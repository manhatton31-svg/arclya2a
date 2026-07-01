"""Tool Registry — discover and validate agent tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from arclya2a.connectors import CONNECTORS
from arclya2a.connectors.base import dry_run_enabled, env_present


class ToolRegistry:
    """Loads tool definitions and resolves availability per agent."""

    def __init__(self, root: Path):
        self.root = root
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        path = self.root / "config" / "tools.json"
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self._data.get("tools", []))

    def get_tool(self, tool_id: str) -> dict[str, Any] | None:
        return next((t for t in self.tools if t["id"] == tool_id), None)

    def _connector_available(self, tool: dict[str, Any]) -> bool:
        if dry_run_enabled():
            return bool(CONNECTORS.get(tool.get("connector", "")))
        connector_name = tool.get("connector", "")
        connector_cls = CONNECTORS.get(connector_name)
        if not connector_cls:
            return False
        return connector_cls().is_available(tool)

    def list_for_agent(self, agent_id: str, *, only_available: bool = True) -> list[dict[str, Any]]:
        """Tools an agent may request, optionally filtered to credentialed connectors."""
        allowed = [
            t for t in self.tools
            if agent_id in t.get("allowed_agents", [])
        ]
        if only_available:
            allowed = [t for t in allowed if self._connector_available(t)]
        return allowed

    def catalog_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        """Discovery catalog (includes unavailable tools with status)."""
        rows = []
        for tool in self.tools:
            if agent_id not in tool.get("allowed_agents", []):
                continue
            available = self._connector_available(tool)
            rows.append({
                "id": tool["id"],
                "name": tool["name"],
                "description": tool["description"],
                "connector": tool["connector"],
                "available": available,
                "required_env": tool.get("required_env", []),
                "parameters": tool.get("parameters", {}),
            })
        return rows

    def catalog_json(self, agent_id: str) -> str:
        return json.dumps(self.catalog_for_agent(agent_id), indent=2)

    def validate_request(self, agent_id: str, tool_id: str) -> tuple[dict[str, Any] | None, str | None]:
        tool = self.get_tool(tool_id)
        if not tool:
            return None, f"Unknown tool: {tool_id}"
        if agent_id not in tool.get("allowed_agents", []):
            return None, f"Agent {agent_id} not allowed to use {tool_id}"
        if not self._connector_available(tool) and not dry_run_enabled():
            missing = [k for k in tool.get("required_env", []) if not os.environ.get(k, "").strip()]
            return None, f"Connector unavailable; missing env: {', '.join(missing)}"
        return tool, None

    def summary(self) -> dict[str, Any]:
        """Platform-wide tool capability status."""
        by_connector: dict[str, dict[str, Any]] = {}
        for tool in self.tools:
            conn = tool["connector"]
            if conn not in by_connector:
                sample = next((t for t in self.tools if t["connector"] == conn), tool)
                by_connector[conn] = {
                    "connector": conn,
                    "available": self._connector_available(sample),
                    "tools": [],
                }
            by_connector[conn]["tools"].append(tool["id"])
        return {
            "version": self._data.get("version"),
            "total_tools": len(self.tools),
            "connectors": list(by_connector.values()),
        }
#!/usr/bin/env python3
"""Sync selected env vars from .env to Render (one-off operator tool)."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

SERVICE_ID = "srv-d928a628qa3s73d06iv0"
SYNC_KEYS = {
    "ARCLYA_OPERATOR_KEY",
    "ARCLYA_AGENT_EMAIL_DELIVERY",
    "ARCLYA_AGENT_EMAIL_SMTP_URL",
    "ARCLYA_AGENT_EMAIL_FROM",
    "ARCLYA_PUBLIC_URL",
}


def load_env(path: Path) -> tuple[str | None, dict[str, str]]:
    api_key: str | None = None
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key == "RENDER_API_KEY":
            api_key = value
        if key in SYNC_KEYS:
            values[key] = value
    values.setdefault("ARCLYA_PUBLIC_URL", "https://arclya2a.onrender.com")
    return api_key, values


def api_request(*, api_key: str, method: str, url: str, body: dict | None = None) -> int:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    if not env_path.exists():
        print("ERROR: .env not found", file=sys.stderr)
        return 1

    api_key, values = load_env(env_path)
    if not api_key:
        print("ERROR: RENDER_API_KEY missing from .env", file=sys.stderr)
        return 1

    for key, value in sorted(values.items()):
        try:
            status = api_request(
                api_key=api_key,
                method="PUT",
                url=f"https://api.render.com/v1/services/{SERVICE_ID}/env-vars/{key}",
                body={"value": value},
            )
            print(f"PUT {key}: {status}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            print(f"ERROR {key}: HTTP {exc.code} {detail[:300]}", file=sys.stderr)
            return 1

    deploy_status = api_request(
        api_key=api_key,
        method="POST",
        url=f"https://api.render.com/v1/services/{SERVICE_ID}/deploys",
        body={"clearCache": "do_not_clear"},
    )
    print(f"POST deploy: {deploy_status}")
    print("Done — deploy triggered for arclya2a.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
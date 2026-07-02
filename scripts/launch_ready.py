#!/usr/bin/env python3
"""Launch-ready smoke test: full external agent flow on production.

Runs register → verify → profile → directory against a live deployment.
Use after setting SMTP, operator key, and public URL on Render.

Environment:
  ARCLYA_BASE_URL          Target deployment (default: https://arclya2a.onrender.com)
  ARCLYA_OPERATOR_KEY      Operator key for verification-outbox (launch testing)
  ARCLYA_LAUNCH_VERIFY_TOKEN  Optional manual verification token (ev_...)

Usage:
  python scripts/launch_ready.py
  python scripts/launch_ready.py --verify-token ev_...
  ARCLYA_BASE_URL=https://agents.yourdomain.com python scripts/launch_ready.py
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from urllib.parse import parse_qs, urlparse

import httpx

BASE = os.environ.get("ARCLYA_BASE_URL", "https://arclya2a.onrender.com").rstrip("/")
OPERATOR_KEY = os.environ.get("ARCLYA_OPERATOR_KEY", "").strip()
ENV_VERIFY_TOKEN = os.environ.get("ARCLYA_LAUNCH_VERIFY_TOKEN", "").strip()


def _extract_token_from_link(verify_link: str) -> str | None:
    parsed = urlparse(verify_link)
    tokens = parse_qs(parsed.query).get("token", [])
    return tokens[0] if tokens else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch-ready external agent flow smoke test")
    parser.add_argument("--verify-token", default="", help="Verification token (ev_...) if not using operator outbox")
    parser.add_argument("--base-url", default=BASE, help="Production base URL")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    verify_token = (args.verify_token or ENV_VERIFY_TOKEN).strip()

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        status = "PASS" if ok else "FAIL"
        suffix = f" — {detail}" if detail else ""
        print(f"{status} {name}{suffix}")

    suffix = uuid.uuid4().hex[:8]
    agent_name = f"Launch_{suffix}"
    email = f"launch_{suffix}@arclya-launch.test"

    with httpx.Client(timeout=90.0, follow_redirects=True) as client:
        # Platform readiness
        r = client.get(f"{base}/health")
        health = r.json() if r.status_code == 200 else {}
        check("GET /health", r.status_code == 200, health.get("status", ""))

        r = client.get(f"{base}/status")
        status = r.json() if r.status_code == 200 else {}
        check("GET /status", r.status_code == 200)

        email_health = (status.get("component_health") or {}).get("email") or {}
        launch_ready = status.get("launch_readiness", {}).get("ready", False)
        check(
            "email delivery configured",
            email_health.get("delivery_mode_effective") == "smtp",
            email_health.get("status", "unknown"),
        )
        check(
            "launch_readiness.ready",
            launch_ready,
            "true when SMTP + crypto configured" if not launch_ready else "ready",
        )

        public_url = (status.get("platform_summary") or {}).get("public_url", base)
        check("public URL resolved", bool(public_url), public_url)

        r = client.get(f"{base}/.well-known/agent-card.json")
        card = r.json() if r.status_code == 200 else {}
        check("agent card", r.status_code == 200 and card.get("url") == public_url, card.get("url", ""))

        r = client.get(f"{base}/agents/onboarding/guide")
        guide = r.json() if r.status_code == 200 else {}
        check("onboarding guide", r.status_code == 200, guide.get("version", ""))

        r = client.get(f"{base}/agents/terms")
        terms = r.json() if r.status_code == 200 else {}
        check("terms metadata", r.status_code == 200 and bool(terms.get("version")), terms.get("version", ""))

        # Register
        reg_body = {
            "agent_name": agent_name,
            "email": email,
            "description": f"Launch-ready smoke test agent {suffix}",
            "capabilities": ["a2a_handoff", "recruitment"],
            "accept_terms": True,
        }
        r = client.post(f"{base}/agents/register", json=reg_body)
        reg = r.json() if r.status_code == 200 else {}
        check("POST /agents/register", r.status_code == 200, reg.get("agent_id", r.text[:120]))
        if r.status_code != 200:
            _summary(checks)
            return 1

        agent_id = reg["agent_id"]
        api_key = reg["api_key"]
        ev = reg.get("email_verification") or {}

        # Resolve verification token
        token = verify_token
        if not token:
            token = ev.get("verify_link") and _extract_token_from_link(str(ev["verify_link"]))
        if not token and OPERATOR_KEY:
            r = client.get(
                f"{base}/agents/operator/verification-outbox",
                params={"agent_id": agent_id, "limit": 1},
                headers={"X-Arclya-Operator-Key": OPERATOR_KEY},
            )
            if r.status_code == 200:
                latest = (r.json().get("latest") or {})
                token = latest.get("token") or (
                    _extract_token_from_link(str(latest.get("verify_link", "")))
                    if latest.get("verify_link")
                    else None
                )
                check("operator verification-outbox", bool(token), latest.get("delivery", ""))
            else:
                check("operator verification-outbox", False, f"status={r.status_code}")

        if not token:
            check(
                "verification token available",
                False,
                "Set ARCLYA_OPERATOR_KEY or --verify-token (check inbox when SMTP is live)",
            )
            _summary(checks)
            return 1

        # Verify email
        r = client.post(f"{base}/agents/verify-email", json={"token": token})
        verified = r.json() if r.status_code == 200 else {}
        check(
            "POST /agents/verify-email",
            r.status_code == 200 and verified.get("email_verified") is True,
            verified.get("message", r.text[:120]),
        )
        if r.status_code != 200:
            _summary(checks)
            return 1

        headers = {"X-Arclya-Key": api_key}

        # Profile
        r = client.get(f"{base}/agents/me", headers=headers)
        me = r.json() if r.status_code == 200 else {}
        check("GET /agents/me", r.status_code == 200, me.get("agent_id", ""))

        r = client.patch(
            f"{base}/agents/me",
            headers=headers,
            json={
                "description": f"Launch-ready verified agent {suffix}",
                "capabilities": ["a2a_handoff", "recruitment", "onboarding"],
            },
        )
        check("PATCH /agents/me profile", r.status_code == 200)

        # Directory opt-in
        r = client.patch(
            f"{base}/agents/me",
            headers=headers,
            json={"publicly_listed": True},
        )
        listed = r.json() if r.status_code == 200 else {}
        check(
            "PATCH /agents/me directory opt-in",
            r.status_code == 200 and listed.get("publicly_listed") is True,
            "",
        )

        # Directory listing
        r = client.get(f"{base}/agents/directory", params={"q": agent_name, "limit": 20})
        directory = r.json() if r.status_code == 200 else {}
        ids = [a.get("agent_id") for a in directory.get("agents", [])]
        check(
            "GET /agents/directory",
            r.status_code == 200 and agent_id in ids,
            f"found={agent_id in ids} total={directory.get('total', 0)}",
        )

        # Public profile
        r = client.get(f"{base}/agents/{agent_id}")
        profile = r.json() if r.status_code == 200 else {}
        check(
            "GET /agents/{agent_id}",
            r.status_code == 200 and profile.get("agent_id") == agent_id,
            profile.get("agent_name", ""),
        )

        if ev.get("delivery") == "smtp":
            check("SMTP verification email sent", ev.get("sent") is True, ev.get("delivery", ""))

    _summary(checks)
    failed = [c for c in checks if not c[1]]
    return 1 if failed else 0


def _summary(checks: list[tuple[str, bool, str]]) -> None:
    failed = [c for c in checks if not c[1]]
    print("---")
    print(f"Launch ready: {len(checks) - len(failed)}/{len(checks)} passed")
    if failed:
        print("Failed:")
        for name, _, detail in failed:
            print(f"  - {name}" + (f" ({detail})" if detail else ""))


if __name__ == "__main__":
    raise SystemExit(main())
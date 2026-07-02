#!/usr/bin/env python3
"""Production smoke test for arclya2a.onrender.com."""

from __future__ import annotations

import os
import sys

import httpx

BASE = os.environ.get("ARCLYA_BASE_URL", "https://arclya2a.onrender.com").rstrip("/")


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        status = "PASS" if ok else "FAIL"
        suffix = f" — {detail}" if detail else ""
        print(f"{status} {name}{suffix}")

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        r = client.get(f"{BASE}/health")
        check("GET /health", r.status_code == 200, f"status={r.status_code}")

        r = client.get(f"{BASE}/.well-known/agent-card.json")
        card = r.json() if r.status_code == 200 else {}
        check(
            "signed agent card",
            r.status_code == 200 and "signature" in card,
            str(card.get("a2a", {}).get("protocol_version", "")),
        )
        features = card.get("platform", {}).get("features", [])
        check("referral in agent card", "agent_referral_program" in features)

        r = client.get(f"{BASE}/agents/referrals/program")
        prog = r.json() if r.status_code == 200 else {}
        check(
            "referral program live",
            r.status_code == 200 and prog.get("enabled") is True,
            f"reward={prog.get('reward_usd')} USDC",
        )
        check(
            "directory opt-in required",
            prog.get("qualification", {}).get("directory_opt_in") is True,
        )

        r = client.get(f"{BASE}/agents/hangout")
        hang = r.json() if r.status_code == 200 else {}
        check(
            "agent hangout",
            r.status_code == 200 and hang.get("constitutional", {}).get("inference") == "xai_only",
        )

        r = client.get(f"{BASE}/agents/referrals/invite")
        check("invite landing", r.status_code == 200 and "program" in r.json())

        r = client.get(f"{BASE}/payments/crypto/x402/facilitators")
        if r.status_code == 200:
            check("x402 facilitators", r.json().get("x402Version") == 2)
        else:
            check("x402 facilitators", r.status_code in (200, 503), f"status={r.status_code}")

        r = client.get(f"{BASE}/agents/onboarding/guide")
        guide = r.json() if r.status_code == 200 else {}
        check("onboarding guide v1.8.0", r.status_code == 200 and guide.get("version") == "1.8.0")

    failed = [c for c in checks if not c[1]]
    print("---")
    print(f"Smoke: {len(checks) - len(failed)}/{len(checks)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
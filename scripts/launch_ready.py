#!/usr/bin/env python3
"""Launch-ready smoke test: full external agent flow on production or local.

Runs register → verify → profile → directory against a live deployment.
Use after setting SMTP, operator key, and public URL on Render.

Environment:
  ARCLYA_BASE_URL            Target deployment (default: https://arclya2a.onrender.com)
  ARCLYA_OPERATOR_KEY        Operator key for verification-outbox (launch testing)
  ARCLYA_LAUNCH_VERIFY_TOKEN Optional manual verification token (ev_...)

Usage:
  python scripts/launch_ready.py
  python scripts/launch_ready.py --verify-token ev_...
  ARCLYA_BASE_URL=http://127.0.0.1:8787 python scripts/launch_ready.py --local
  ARCLYA_BASE_URL=https://agents.yourdomain.com python scripts/launch_ready.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from arclya2a.agents.onboarding_guide import GUIDE_VERSION

DEFAULT_BASE = os.environ.get("ARCLYA_BASE_URL", "https://arclya2a.onrender.com").rstrip("/")
OPERATOR_KEY = os.environ.get("ARCLYA_OPERATOR_KEY", "").strip()
ENV_VERIFY_TOKEN = os.environ.get("ARCLYA_LAUNCH_VERIFY_TOKEN", "").strip()

REGISTRATION_DEBUG_HINTS: dict[str, str] = {
    "validation_error": (
        "Check required fields: agent_name, terms_accepted (or accept_terms: true). "
        "See GET /agents/terms and GET /agents/onboarding/guide."
    ),
    "registration_denied": (
        "Rate limit or IP cap hit — wait and retry, or lower ARCLYA_AGENT_MAX_REGISTER_PER_IP_DAY."
    ),
    "terms_accepted": (
        "Terms acceptance is mandatory. Include terms_accepted: true or accept_terms: true in the body."
    ),
}


def build_registration_body(
    *,
    agent_name: str,
    email: str,
    suffix: str,
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Valid POST /agents/register body (terms required since ToS enforcement)."""
    return {
        "agent_name": agent_name,
        "email": email,
        "description": f"Launch-ready smoke test agent {suffix}",
        "capabilities": capabilities or ["a2a_handoff", "recruitment"],
        # Canonical + alias — both accepted by the API
        "terms_accepted": True,
        "accept_terms": True,
    }


def extract_token_from_link(verify_link: str) -> str | None:
    parsed = urlparse(verify_link)
    tokens = parse_qs(parsed.query).get("token", [])
    return tokens[0] if tokens else None


def parse_api_error(response: httpx.Response) -> dict[str, Any]:
    """Extract structured error details from an API response."""
    result: dict[str, Any] = {
        "status_code": response.status_code,
        "message": response.text[:500] if response.text else "(empty body)",
    }
    text = (response.text or "").strip()
    if text.startswith("<"):
        result["message"] = "HTML error page (likely gateway timeout or cold start — retry)"
        return result
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return result

    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            result["code"] = err.get("code")
            result["message"] = err.get("message") or result["message"]
            result["details"] = err.get("details")
        else:
            result["message"] = payload.get("message") or result["message"]
            result["body"] = payload
    return result


def registration_failure_hints(error: dict[str, Any]) -> list[str]:
    """Suggested next steps when POST /agents/register fails."""
    hints: list[str] = []
    code = str(error.get("code") or "")
    if code in REGISTRATION_DEBUG_HINTS:
        hints.append(REGISTRATION_DEBUG_HINTS[code])

    details = error.get("details")
    if isinstance(details, dict):
        fields = details.get("fields")
        if isinstance(fields, list):
            for field_issue in fields:
                if not isinstance(field_issue, dict):
                    continue
                field = field_issue.get("field", "")
                message = field_issue.get("message", "")
                hints.append(f"Fix field '{field}': {message}")
                if field == "terms_accepted":
                    hints.append(REGISTRATION_DEBUG_HINTS["terms_accepted"])

    if error.get("status_code") == 429:
        hints.append(REGISTRATION_DEBUG_HINTS["registration_denied"])
    if not hints:
        hints.append("GET /agents/terms — confirm current terms version")
        hints.append("GET /agents/onboarding/guide — see registration body_example")
        hints.append("Retry with a unique agent_name and email")
    return hints


def print_api_failure(step: str, response: httpx.Response, *, hints: list[str] | None = None) -> None:
    """Print HTTP status, API error, and debugging hints."""
    error = parse_api_error(response)
    print(f"FAIL {step}")
    print(f"  HTTP status: {error.get('status_code')}")
    print(f"  API message: {error.get('message')}")
    if error.get("code"):
        print(f"  Error code:  {error.get('code')}")
    if error.get("details"):
        print(f"  Details:     {json.dumps(error['details'], indent=2)[:800]}")
    for hint in hints or []:
        print(f"  → {hint}")


def is_local_base_url(base_url: str) -> bool:
    host = urlparse(base_url).hostname or ""
    return host in {"127.0.0.1", "localhost", "0.0.0.0"} or host.endswith(".local")


def resolve_verification_token(
    client: httpx.Client,
    *,
    base: str,
    agent_id: str,
    email_verification: dict[str, Any],
    operator_key: str,
    manual_token: str,
    retries: int = 2,
) -> tuple[str | None, list[tuple[str, bool, str]]]:
    """Resolve ev_ token: manual → registration verify_link → operator outbox."""
    extra_checks: list[tuple[str, bool, str]] = []

    if manual_token:
        extra_checks.append(("manual verification token", True, manual_token[:16] + "…"))
        return manual_token, extra_checks

    verify_link = email_verification.get("verify_link")
    if verify_link:
        token = extract_token_from_link(str(verify_link))
        if token:
            extra_checks.append(
                ("registration verify_link (outbox/dev)", True, email_verification.get("delivery", "outbox"))
            )
            return token, extra_checks

    if not operator_key:
        extra_checks.append(
            (
                "operator key configured",
                False,
                "Set ARCLYA_OPERATOR_KEY for production SMTP (token not in registration response)",
            )
        )
        return None, extra_checks

    last_detail = ""
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(2 * attempt)
        try:
            r = client.get(
                f"{base}/agents/operator/verification-outbox",
                params={"agent_id": agent_id, "limit": 1},
                headers={"X-Arclya-Operator-Key": operator_key},
            )
        except httpx.HTTPError as exc:
            last_detail = str(exc)
            continue

        if r.status_code == 200:
            latest = (r.json().get("latest") or {})
            token = latest.get("token") or (
                extract_token_from_link(str(latest.get("verify_link", "")))
                if latest.get("verify_link")
                else None
            )
            extra_checks.append(
                ("operator verification-outbox", bool(token), latest.get("delivery", ""))
            )
            return token, extra_checks

        err = parse_api_error(r)
        last_detail = f"status={r.status_code} code={err.get('code')} msg={str(err.get('message', ''))[:120]}"
        if r.status_code in {502, 503, 504} and attempt < retries:
            continue
        extra_checks.append(("operator verification-outbox", False, last_detail))
        if r.status_code == 401:
            extra_checks.append(
                (
                    "operator key valid",
                    False,
                    "ARCLYA_OPERATOR_KEY must match the value on the target host",
                )
            )
        break

    return None, extra_checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch-ready external agent flow smoke test")
    parser.add_argument("--verify-token", default="", help="Verification token (ev_...) if not using operator outbox")
    parser.add_argument("--base-url", default=DEFAULT_BASE, help="Deployment base URL")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Relax launch gates (skip SMTP/launch_readiness requirements; for localhost/outbox)",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    local_mode = args.local or is_local_base_url(base)
    verify_token = (args.verify_token or ENV_VERIFY_TOKEN).strip()
    operator_key = OPERATOR_KEY

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
        r = client.get(f"{base}/health")
        health = r.json() if r.status_code == 200 else {}
        check("GET /health", r.status_code == 200, health.get("status", ""))

        r = client.get(f"{base}/status")
        status_payload = r.json() if r.status_code == 200 else {}
        check("GET /status", r.status_code == 200)

        email_health = (status_payload.get("component_health") or {}).get("email") or {}
        launch_ready = status_payload.get("launch_readiness", {}).get("ready", False)
        delivery_mode = email_health.get("delivery_mode_effective", "unknown")

        if local_mode:
            check(
                "email delivery (local mode)",
                delivery_mode in {"outbox", "smtp", "unknown"},
                f"mode={delivery_mode} — SMTP gate skipped",
            )
            check("launch_readiness (local mode)", True, "skipped in --local / localhost")
        else:
            check(
                "email delivery configured",
                email_health.get("delivery_mode_effective") == "smtp",
                email_health.get("status", "unknown"),
            )
            check(
                "launch_readiness.ready",
                launch_ready,
                "true when SMTP + crypto + operator key configured" if not launch_ready else "ready",
            )

        public_url = (status_payload.get("platform_summary") or {}).get("public_url", base)
        check("public URL resolved", bool(public_url), public_url)

        r = client.get(f"{base}/.well-known/agent-card.json")
        card = r.json() if r.status_code == 200 else {}
        check("agent card", r.status_code == 200 and card.get("url") == public_url, card.get("url", ""))

        r = client.get(f"{base}/agents/onboarding/guide")
        guide = r.json() if r.status_code == 200 else {}
        check(
            f"onboarding guide v{GUIDE_VERSION}",
            r.status_code == 200 and guide.get("version") == GUIDE_VERSION,
            guide.get("version", ""),
        )

        r = client.get(f"{base}/agents/terms")
        terms = r.json() if r.status_code == 200 else {}
        check("terms metadata", r.status_code == 200 and bool(terms.get("version")), terms.get("version", ""))

        reg_body = build_registration_body(agent_name=agent_name, email=email, suffix=suffix)
        r = None
        for attempt in range(3):
            r = client.post(f"{base}/agents/register", json=reg_body)
            if r.status_code == 200:
                break
            if r.status_code in {502, 503, 504} and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            break
        assert r is not None
        if r.status_code != 200:
            error = parse_api_error(r)
            hints = registration_failure_hints(error)
            print_api_failure("POST /agents/register", r, hints=hints)
            checks.append(("POST /agents/register", False, error.get("message", "")[:120]))
            _summary(checks)
            return 1

        reg = r.json()
        check("POST /agents/register", True, reg.get("agent_id", ""))
        if not reg.get("terms_accepted"):
            check("terms accepted at registration", False, "terms_accepted missing in response")

        agent_id = reg["agent_id"]
        api_key = reg["api_key"]
        ev = reg.get("email_verification") or {}

        if ev.get("queued"):
            time.sleep(3)
        else:
            time.sleep(1)
        token, token_checks = resolve_verification_token(
            client,
            base=base,
            agent_id=agent_id,
            email_verification=ev,
            operator_key=operator_key,
            manual_token=verify_token,
            retries=3,
        )
        for name, ok, detail in token_checks:
            check(name, ok, detail)

        if not token:
            hints = [
                "Production SMTP: check the inbox for the verification email and pass --verify-token ev_...",
                "Or set ARCLYA_OPERATOR_KEY matching the target host and retry",
                "Local/outbox: run with --local and ARCLYA_AGENT_EMAIL_DELIVERY=outbox — verify_link is in the response",
            ]
            check(
                "verification token available",
                False,
                "; ".join(hints[:2]),
            )
            _summary(checks)
            return 1

        check("verification token available", True, token[:16] + "…")

        r = client.post(f"{base}/agents/verify-email", json={"token": token})
        if r.status_code != 200:
            print_api_failure(
                "POST /agents/verify-email",
                r,
                hints=[
                    "Token may be expired — POST /agents/me/resend-verification with the agent API key",
                    "Use a fresh token from operator verification-outbox or the latest email",
                ],
            )
            checks.append(("POST /agents/verify-email", False, parse_api_error(r).get("message", "")[:120]))
            _summary(checks)
            return 1

        verified = r.json()
        check(
            "POST /agents/verify-email",
            verified.get("email_verified") is True,
            verified.get("message", ""),
        )

        headers = {"X-Arclya-Key": api_key}

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

        r = client.get(f"{base}/agents/directory", params={"q": agent_name, "limit": 20})
        directory = r.json() if r.status_code == 200 else {}
        ids = [a.get("agent_id") for a in directory.get("agents", [])]
        check(
            "GET /agents/directory",
            r.status_code == 200 and agent_id in ids,
            f"found={agent_id in ids} total={directory.get('total', 0)}",
        )

        r = client.get(f"{base}/agents/{agent_id}")
        profile = r.json() if r.status_code == 200 else {}
        check(
            "GET /agents/{agent_id}",
            r.status_code == 200 and profile.get("agent_id") == agent_id,
            profile.get("agent_name", ""),
        )

        if ev.get("delivery") == "smtp":
            smtp_ok = ev.get("sent") is True or ev.get("queued") is True
            check(
                "SMTP verification email queued/sent",
                smtp_ok,
                ev.get("message", ev.get("delivery", "")),
            )

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
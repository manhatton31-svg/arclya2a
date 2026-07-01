#!/usr/bin/env python3
"""Operator helper: health checks and optional chaining for first crypto sale runbook.

Wraps existing scripts/modules with progress reporting. The runbook is authoritative:
docs/first-crypto-sale-runbook.md

Usage
-----
    python scripts/run_first_crypto_sale.py check
    python scripts/run_first_crypto_sale.py rehearse
    python scripts/run_first_crypto_sale.py graduate --partner-id tp_abc123
    python scripts/run_first_crypto_sale.py payments
    python scripts/run_first_crypto_sale.py confirm --payment-id cpay_x --tx-hash 0x...
    python scripts/run_first_crypto_sale.py verify --payment-id cpay_x --partner-id tp_abc --deal-id deal_1
    python scripts/run_first_crypto_sale.py run --partner-id tp_abc123 --skip-rehearse

    # Remote crypto sale (intent → send USDC → submit → confirm):
    python scripts/run_first_crypto_sale.py sale start --partner-id tp_abc --amount 1 --network base
    python scripts/run_first_crypto_sale.py sale submit --tx-hash 0x...
    python scripts/run_first_crypto_sale.py sale confirm
    python scripts/run_first_crypto_sale.py sale status
    python scripts/run_first_crypto_sale.py sale next
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from arclya2a.partners.graduation import (  # noqa: E402
    assess_graduation_readiness,
    graduate_partner,
)
from arclya2a.server.operator_auth import load_operator_key  # noqa: E402

import confirm_crypto_payment as crypto_cli  # noqa: E402
import crypto_sale_flow as sale_flow  # noqa: E402
import sandbox_partner_rehearsal as rehearsal  # noqa: E402

RUNBOOK_PATH = ROOT / "docs" / "first-crypto-sale-runbook.md"


class HttpClient(Protocol):
    def get(self, url: str, *, headers: dict[str, str] | None = None) -> Any: ...
    def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any: ...


def resolve_base_url() -> str:
    return os.environ.get("ARCLYA_BASE_URL", "http://127.0.0.1:8787").rstrip("/")


def _step(label: str, ok: bool, detail: str = "") -> dict[str, Any]:
    mark = "OK" if ok else "FAIL"
    line = f"  [{mark:4}] {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return {"name": label, "ok": ok, "detail": detail}


def check_local_prerequisites() -> tuple[bool, list[dict[str, Any]]]:
    """Validate operator env vars (local side)."""
    steps: list[dict[str, Any]] = []
    base_url = resolve_base_url()
    steps.append(_step("ARCLYA_BASE_URL", bool(base_url), base_url))

    operator_key = load_operator_key()
    op_ok = bool(operator_key and len(operator_key) >= 8)
    steps.append(
        _step(
            "ARCLYA_OPERATOR_KEY",
            op_ok,
            "set (min 8 chars)" if op_ok else "missing or too short",
        )
    )

    runbook_ok = RUNBOOK_PATH.is_file()
    steps.append(_step("Runbook present", runbook_ok, str(RUNBOOK_PATH)))

    return all(s["ok"] for s in steps), steps


def check_remote_health(client: HttpClient, base_url: str) -> tuple[bool, list[dict[str, Any]]]:
    """Ping live instance health and crypto configuration."""
    steps: list[dict[str, Any]] = []

    health = client.get(f"{base_url}/health")
    health_ok = health.status_code == 200
    status = "?"
    if health_ok:
        try:
            status = health.json().get("status", "?")
        except Exception:
            status = "parse_error"
    health_acceptable = status in ("healthy", "degraded")
    detail = f"status={status}"
    if status == "degraded":
        detail += " (acceptable; investigate before production traffic)"
    steps.append(_step("GET /health", health_ok and health_acceptable, detail))

    networks = client.get(f"{base_url}/payments/crypto/networks")
    net_ok = networks.status_code == 200
    enabled = False
    net_count = 0
    if net_ok:
        try:
            payload = networks.json()
            enabled = bool(payload.get("enabled"))
            net_list = payload.get("networks") or []
            net_count = len(net_list)
        except Exception:
            net_ok = False
    steps.append(
        _step(
            "GET /payments/crypto/networks",
            net_ok and enabled and net_count > 0,
            f"enabled={enabled}, networks={net_count}" if net_ok else f"HTTP {networks.status_code}",
        )
    )

    dashboard = client.get(f"{base_url}/ops/dashboard")
    dash_ok = dashboard.status_code == 200
    has_payments = False
    if dash_ok:
        try:
            has_payments = "payments" in dashboard.json()
        except Exception:
            dash_ok = False
    steps.append(
        _step(
            "GET /ops/dashboard",
            dash_ok and has_payments,
            "payments section present" if has_payments else f"HTTP {dashboard.status_code}",
        )
    )

    return all(s["ok"] for s in steps), steps


def run_check(client: HttpClient | None = None, *, base_url: str | None = None) -> dict[str, Any]:
    """Run local + remote prerequisite checks."""
    base = (base_url or resolve_base_url()).rstrip("/")
    print("=" * 72)
    print("First Crypto Sale — Prerequisites Check")
    print("=" * 72)
    print(f"  Target: {base}")
    print(f"  Runbook: {RUNBOOK_PATH}")
    print("")

    local_ok, local_steps = check_local_prerequisites()
    print("")
    print("── Remote health ──")

    remote_steps: list[dict[str, Any]] = []
    remote_ok = False
    if client is not None:
        remote_ok, remote_steps = check_remote_health(client, base)
    else:
        try:
            import httpx

            with httpx.Client(timeout=30.0) as http:
                adapter = _HttpxAdapter(http)
                remote_ok, remote_steps = check_remote_health(adapter, base)
        except ImportError:
            remote_steps.append(
                _step("Remote checks", False, "httpx not installed (pip install httpx)")
            )

    ok = local_ok and remote_ok
    print("")
    print("=" * 72)
    print(f"  Result: {'GO' if ok else 'NO-GO'}")
    print("=" * 72)
    return {
        "ok": ok,
        "base_url": base,
        "local_steps": local_steps,
        "remote_steps": remote_steps,
    }


def run_rehearse_step(
    client: HttpClient | None = None,
    *,
    base_url: str | None = None,
    sandbox_key: str | None = None,
) -> dict[str, Any]:
    """Run sandbox partner rehearsal."""
    base = (base_url or resolve_base_url()).rstrip("/")
    print("=" * 72)
    print("First Crypto Sale — Sandbox Rehearsal")
    print("=" * 72)

    report = rehearsal.run_rehearsal(
        base_url=base,
        sandbox_key=sandbox_key or os.environ.get("ARCLYA_SANDBOX_KEY", "").strip() or None,
        http_client=client,
        quiet=True,
    )
    print(rehearsal.format_graduation_report(report))
    return report


def _graduate_via_http(
    client: HttpClient,
    base_url: str,
    partner_id: str,
    *,
    operator_key: str,
    performed_by: str,
    check_only: bool = False,
) -> dict[str, Any]:
    """Graduate (or check readiness) via POST /partners/graduate on the live server."""
    headers = {"X-Arclya-Operator-Key": operator_key, "X-Arclya-Operator-Id": performed_by}
    if check_only:
        test_resp = client.get(f"{base_url}/partners/test")
        if test_resp.status_code != 200:
            return {"ok": False, "error": f"partners/test HTTP {test_resp.status_code}"}
        partners = test_resp.json().get("partners") or []
        match = next((p for p in partners if p.get("partner_id") == partner_id), None)
        if not match:
            return {"ok": False, "error": f"partner not found: {partner_id}"}
        ready = bool(match.get("graduation_ready"))
        print(f"  Graduation ready: {ready}")
        if not ready:
            blockers = match.get("blocking_issues") or match.get("reasons") or []
            for reason in blockers:
                print(f"    • {reason}")
        return {"ok": ready, "check_only": True, "partner": match}

    resp = client.post(
        f"{base_url}/partners/graduate",
        json={"partner_id": partner_id, "performed_by": performed_by},
        headers=headers,
    )
    try:
        payload = resp.json()
    except Exception:
        payload = {"raw": getattr(resp, "text", str(resp))}
    if resp.status_code not in (200, 201):
        message = payload.get("error", {}).get("message") if isinstance(payload, dict) else str(payload)
        reasons = (
            payload.get("error", {}).get("details", {}).get("blocking_reasons")
            if isinstance(payload, dict)
            else None
        )
        print(f"ERROR: Graduation failed (HTTP {resp.status_code}): {message}", file=sys.stderr)
        if reasons:
            for reason in reasons:
                print(f"  • {reason}", file=sys.stderr)
        return {"ok": False, "error": message, "status_code": resp.status_code}
    print(f"  Graduated:      {payload.get('partner_id', partner_id)}")
    print(f"  Production key: {payload.get('production_key', '?')}")
    print(f"  Audit ID:       {payload.get('audit_id', '?')}")
    print("  Store the production key securely — shown once.")
    return {"ok": True, "result": payload}


def run_graduate_step(
    partner_id: str,
    *,
    client: HttpClient | None = None,
    base_url: str | None = None,
    performed_by: str | None = None,
    check_only: bool = False,
) -> dict[str, Any]:
    """Check or execute partner graduation (HTTP API when client provided)."""
    operator_key = load_operator_key()
    if not operator_key or len(operator_key) < 8:
        print("ERROR: ARCLYA_OPERATOR_KEY required (min 8 chars).", file=sys.stderr)
        return {"ok": False, "error": "missing_operator_key"}

    print("=" * 72)
    print("First Crypto Sale — Partner Graduation")
    print("=" * 72)
    print(f"  Partner ID: {partner_id}")
    print("")

    who = performed_by or os.environ.get("ARCLYA_OPERATOR_ID", "first_sale_runbook")
    base = (base_url or resolve_base_url()).rstrip("/")

    if client is not None:
        return _graduate_via_http(
            client,
            base,
            partner_id,
            operator_key=operator_key,
            performed_by=who,
            check_only=check_only,
        )

    assessment = assess_graduation_readiness(ROOT, partner_id)
    if check_only:
        ready = bool(assessment.get("ready"))
        print(f"  Ready: {ready}")
        if not ready:
            for reason in assessment.get("reasons") or []:
                print(f"    • {reason}")
        return {"ok": ready, "check_only": True, "assessment": assessment}

    if not assessment.get("ready"):
        print("Graduation blocked:")
        for reason in assessment.get("reasons") or []:
            print(f"  • {reason}")
        return {"ok": False, "assessment": assessment}

    result = graduate_partner(ROOT, partner_id=partner_id, graduated_by=who)
    print(f"  Graduated:     {result['partner_id']}")
    print(f"  Production key: {result['production_key']}")
    print(f"  Audit ID:      {result['audit_id']}")
    print("  Store the production key securely — shown once.")
    return {"ok": True, "result": result}


def run_payments_step(client: HttpClient, base_url: str | None = None) -> dict[str, Any]:
    """List crypto payments needing review."""
    base = (base_url or resolve_base_url()).rstrip("/")
    summary = crypto_cli.fetch_pending_review(client, base)
    enriched = crypto_cli.enrich_pending_rows(ROOT, summary.get("pending_review") or [])
    print(crypto_cli.format_pending_list(summary, enriched=enriched))
    return {**summary, "pending_review": enriched}


def run_confirm_step(
    client: HttpClient,
    payment_id: str,
    *,
    tx_hash: str | None = None,
    confirmed_by: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Confirm a crypto payment after on-chain verification."""
    base = (base_url or resolve_base_url()).rstrip("/")
    operator_key = load_operator_key()
    if not operator_key or len(operator_key) < 8:
        print("ERROR: ARCLYA_OPERATOR_KEY required.", file=sys.stderr)
        return {"ok": False, "error": "missing_operator_key"}

    who = confirmed_by or os.environ.get("ARCLYA_OPERATOR_ID", "first_sale_runbook")
    result = crypto_cli.confirm_payment_remote(
        client,
        base,
        payment_id,
        operator_key=operator_key,
        tx_hash=tx_hash,
        confirmed_by=who,
    )
    print(crypto_cli.format_confirm_result(payment_id, result))
    return {"ok": True, "result": result}


def run_verify_step(
    client: HttpClient,
    payment_id: str,
    *,
    partner_id: str | None = None,
    deal_id: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Verify confirmed payment, ops dashboard, and attribution."""
    base = (base_url or resolve_base_url()).rstrip("/")
    print("=" * 72)
    print("First Crypto Sale — Post-Confirm Verification")
    print("=" * 72)

    steps: list[dict[str, Any]] = []

    pay_resp = client.get(f"{base}/payments/crypto/{payment_id}")
    pay_ok = pay_resp.status_code == 200
    payment: dict[str, Any] = {}
    if pay_ok:
        try:
            payload = pay_resp.json()
            payment = payload.get("payment") or payload
            pay_ok = payment.get("status") == "confirmed"
        except Exception:
            pay_ok = False
    steps.append(
        _step(
            "Payment confirmed",
            pay_ok,
            f"status={payment.get('status', '?')}, HTTP {pay_resp.status_code}",
        )
    )

    if partner_id:
        match = payment.get("partner_id") == partner_id
        steps.append(_step("partner_id attribution", match, f"expected={partner_id}, got={payment.get('partner_id')}"))
    if deal_id:
        match = payment.get("deal_id") == deal_id
        steps.append(_step("deal_id attribution", match, f"expected={deal_id}, got={payment.get('deal_id')}"))

    dash_resp = client.get(f"{base}/ops/dashboard")
    dash_ok = dash_resp.status_code == 200
    confirmed_count = 0
    if dash_ok:
        try:
            payments = dash_resp.json().get("payments") or {}
            confirmed_count = (payments.get("by_status") or {}).get("confirmed", 0)
        except Exception:
            dash_ok = False
    steps.append(
        _step(
            "Ops dashboard payments",
            dash_ok and confirmed_count >= 1,
            f"confirmed_count={confirmed_count}",
        )
    )

    tx_ok = bool(payment.get("tx_hash"))
    steps.append(_step("tx_hash recorded", tx_ok, payment.get("tx_hash") or "missing"))

    ok = all(s["ok"] for s in steps)
    print("")
    print("=" * 72)
    print(f"  Verification: {'PASS' if ok else 'FAIL'}")
    print("=" * 72)
    if not ok:
        print("  See docs/first-crypto-sale-runbook.md Phase 7 for manual checks.")
    return {"ok": ok, "steps": steps, "payment": payment}


def print_agent_crypto_instructions(partner_id: str | None = None) -> None:
    """Print next steps for the partner agent (on-chain payment is manual)."""
    print("")
    print("── Agent next steps (manual / partner-side) ──")
    print("  1. POST /payments/crypto/intent  (include partner_id + deal_id)")
    if partner_id:
        print(f"     partner_id: {partner_id}")
    print("  2. Send USDC on-chain to wallet_address from response")
    print("  3. POST /payments/crypto/{payment_id}/submit  with tx_hash")
    print("  4. Operator: python scripts/run_first_crypto_sale.py confirm --payment-id ... --tx-hash ...")
    print("  Docs: docs/first-crypto-sale-runbook.md Phase 3–5")


def run_full_operator_flow(
    client: HttpClient,
    *,
    partner_id: str | None = None,
    skip_rehearse: bool = False,
    sandbox_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Chain check → rehearse → graduate, then print agent crypto instructions."""
    base = (base_url or resolve_base_url()).rstrip("/")
    report: dict[str, Any] = {"base_url": base, "steps": []}

    check = run_check(client, base_url=base)
    report["steps"].append({"name": "check", "ok": check["ok"]})
    if not check["ok"]:
        report["ok"] = False
        return report

    resolved_partner = partner_id
    if not skip_rehearse:
        rehearsal_report = run_rehearse_step(client, base_url=base, sandbox_key=sandbox_key)
        report["rehearsal"] = rehearsal_report
        ready = bool(rehearsal_report.get("graduation_ready"))
        report["steps"].append({"name": "rehearse", "ok": ready})
        if not ready:
            report["ok"] = False
            return report
        resolved_partner = resolved_partner or rehearsal_report.get("partner_id")
        if not resolved_partner:
            progress = rehearsal_report.get("progress") or {}
            resolved_partner = progress.get("partner_id")

    if not resolved_partner:
        print("ERROR: --partner-id required (or run rehearsal to discover partner_id).", file=sys.stderr)
        report["ok"] = False
        return report

    grad = run_graduate_step(
        resolved_partner,
        client=client,
        base_url=base,
    )
    report["steps"].append({"name": "graduate", "ok": grad.get("ok", False)})
    report["partner_id"] = resolved_partner
    report["ok"] = grad.get("ok", False)

    if report["ok"]:
        print_agent_crypto_instructions(resolved_partner)
    return report


class _HttpxAdapter:
    def __init__(self, client: Any):
        self._client = client

    def get(self, url: str, *, headers: dict[str, str] | None = None):
        return self._client.get(url, headers=headers)

    def post(self, url: str, *, json=None, headers: dict[str, str] | None = None):
        return self._client.post(url, json=json, headers=headers)


def _build_client() -> tuple[Any, HttpClient]:
    try:
        import httpx
    except ImportError:
        print("ERROR: httpx required. pip install httpx", file=sys.stderr)
        raise SystemExit(2)
    http = httpx.Client(timeout=120.0)
    return http, _HttpxAdapter(http)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Operator helper for first production partner + first crypto sale",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="check",
        choices=[
            "check",
            "rehearse",
            "graduate",
            "payments",
            "confirm",
            "verify",
            "run",
            "guide",
            "sale",
        ],
        help="Workflow step (default: check)",
    )
    parser.add_argument(
        "sale_action",
        nargs="?",
        choices=["start", "submit", "confirm", "status", "next", "instructions"],
        help="With 'sale': start | submit | confirm | status | next | instructions",
    )
    parser.add_argument("--partner-id", help="Partner ID for graduate / verify / sale")
    parser.add_argument("--payment-id", help="Payment ID for confirm / verify / sale")
    parser.add_argument("--deal-id", help="Expected deal_id for verify / sale attribution")
    parser.add_argument("--tx-hash", help="On-chain tx hash for confirm / sale submit")
    parser.add_argument(
        "--amount",
        type=float,
        default=None,
        help="USD amount for sale start (default: 1.0 or ARCLYA_SALE_AMOUNT_USD)",
    )
    parser.add_argument(
        "--network",
        default=sale_flow.DEFAULT_NETWORK,
        help=f"USDC network for sale start (default: {sale_flow.DEFAULT_NETWORK})",
    )
    parser.add_argument(
        "--production-key",
        default=os.environ.get("ARCLYA_PRODUCTION_KEY", "").strip() or None,
        help="Graduated partner production key (default: ARCLYA_PRODUCTION_KEY)",
    )
    parser.add_argument("--sandbox-key", help="Existing sandbox key for rehearsal")
    parser.add_argument("--skip-rehearse", action="store_true", help="Skip rehearsal in run command")
    parser.add_argument("--check-only", action="store_true", help="Graduate: readiness check only")
    parser.add_argument(
        "--base-url",
        default=resolve_base_url(),
        help="Arclya server URL",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, http_client: HttpClient | None = None) -> int:
    args = _parse_args(argv)

    if args.command == "guide":
        print(RUNBOOK_PATH)
        return 0 if RUNBOOK_PATH.is_file() else 1

    owns_http = False
    client = http_client
    http = None
    sale_actions = ("start", "submit", "confirm", "status", "next", "instructions")
    needs_http = args.command in (
        "check",
        "rehearse",
        "graduate",
        "payments",
        "confirm",
        "verify",
        "run",
        "sale",
    )
    if client is None and needs_http:
        http, client = _build_client()
        owns_http = True

    try:
        if args.command == "sale":
            action = args.sale_action or "next"
            if action not in sale_actions:
                print("ERROR: sale requires action: start|submit|confirm|status|next", file=sys.stderr)
                return 2
            if action in ("start", "submit", "confirm", "status") and client is None:
                print("ERROR: HTTP client required for sale actions.", file=sys.stderr)
                return 2
            if action == "start":
                result = sale_flow.run_sale_start(
                    client,
                    base_url=args.base_url,
                    partner_id=args.partner_id,
                    amount_usd=args.amount,
                    network=args.network,
                    deal_id=args.deal_id,
                    production_key=args.production_key,
                )
            elif action == "submit":
                if not args.tx_hash:
                    print("ERROR: --tx-hash required for sale submit.", file=sys.stderr)
                    return 2
                result = sale_flow.run_sale_submit(
                    client,
                    args.tx_hash,
                    base_url=args.base_url,
                    payment_id=args.payment_id,
                    production_key=args.production_key,
                )
            elif action == "confirm":
                result = sale_flow.run_sale_confirm(
                    client,
                    base_url=args.base_url,
                    payment_id=args.payment_id,
                    tx_hash=args.tx_hash,
                )
            elif action == "status":
                result = sale_flow.run_sale_status(
                    client,
                    base_url=args.base_url,
                    payment_id=args.payment_id,
                )
            elif action == "instructions":
                state = sale_flow.load_state()
                payment = state.get("payment") or {}
                if not payment.get("wallet_address"):
                    print("ERROR: No active sale. Run: sale start", file=sys.stderr)
                    return 2
                print(
                    sale_flow.format_send_instructions(
                        payment,
                        base_url=state.get("base_url") or args.base_url,
                        partner_id=state.get("partner_id"),
                    )
                )
                result = {"ok": True, "state": state}
            else:
                result = sale_flow.run_sale_next()
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            return 0 if result.get("ok") else 1
        elif args.command == "check":
            result = run_check(client, base_url=args.base_url)
        elif args.command == "rehearse":
            result = run_rehearse_step(client, base_url=args.base_url, sandbox_key=args.sandbox_key)
            if args.json:
                print(json.dumps(result, indent=2, default=str))
                return 0 if result.get("graduation_ready") else 1
            return 0 if result.get("graduation_ready") else 1
        elif args.command == "graduate":
            if not args.partner_id:
                print("ERROR: --partner-id required for graduate.", file=sys.stderr)
                return 2
            result = run_graduate_step(
                args.partner_id,
                client=client,
                base_url=args.base_url,
                check_only=args.check_only,
            )
            return 0 if result.get("ok") else 1
        elif args.command == "payments":
            result = run_payments_step(client, base_url=args.base_url)
        elif args.command == "confirm":
            if not args.payment_id:
                print("ERROR: --payment-id required for confirm.", file=sys.stderr)
                return 2
            result = run_confirm_step(
                client,
                args.payment_id,
                tx_hash=args.tx_hash,
                base_url=args.base_url,
            )
            return 0 if result.get("ok") else 1
        elif args.command == "verify":
            if not args.payment_id:
                print("ERROR: --payment-id required for verify.", file=sys.stderr)
                return 2
            result = run_verify_step(
                client,
                args.payment_id,
                partner_id=args.partner_id,
                deal_id=args.deal_id,
                base_url=args.base_url,
            )
            return 0 if result.get("ok") else 1
        elif args.command == "run":
            result = run_full_operator_flow(
                client,
                partner_id=args.partner_id,
                skip_rehearse=args.skip_rehearse,
                sandbox_key=args.sandbox_key,
                base_url=args.base_url,
            )
            return 0 if result.get("ok") else 1
        else:
            return 2

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok", result.get("graduation_ready", True)) else 1
    finally:
        if owns_http and http is not None:
            http.close()


if __name__ == "__main__":
    raise SystemExit(main())
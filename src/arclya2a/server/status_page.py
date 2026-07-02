"""Public HTML status page for platform health before sign-up."""

from __future__ import annotations

from typing import Any


def _badge_class(status: str) -> str:
    if status in {"healthy", "available", "ready", "smtp"}:
        return "ok"
    if status in {"degraded", "dev_mode", "disabled", "pending_configuration"}:
        return "warn"
    return "warn"


def build_status_page_html(*, snapshot: dict[str, Any]) -> str:
    """Render a visitor-friendly platform status page."""
    platform = snapshot.get("platform_summary", {})
    agents = snapshot.get("external_agents", {})
    accounts = agents.get("accounts", {})
    activity = agents.get("activity_24h", {})
    components = snapshot.get("component_health", {})
    email_h = components.get("email", {})
    crypto_h = components.get("crypto", {})
    payments = snapshot.get("payments", {})
    pay_summary = platform.get("payments", {})
    launch = snapshot.get("launch_readiness", {})

    status = snapshot.get("status", platform.get("status", "healthy"))
    public_url = platform.get("public_url", "")
    status_class = _badge_class(status)
    agents_status = agents.get("status", "available")
    launch_ready = platform.get("launch_ready", launch.get("ready", False))
    launch_class = "ok" if launch_ready else "warn"

    suspicious = activity.get("suspicious_events", 0)
    email_status = email_h.get("status", "unknown")
    crypto_status = crypto_h.get("status", "unknown")

    blocking_issues = launch.get("blocking_issues") or components.get("blocking_issues") or []
    issues_html = ""
    if blocking_issues:
        items = "".join(f"<li>{issue}</li>" for issue in blocking_issues[:6])
        issues_html = f"<ul>{items}</ul>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Arclya A2A — Platform Status</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; line-height: 1.5; }}
    body {{ max-width: 52rem; margin: 2rem auto; padding: 0 1.25rem; }}
    h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
    h2 {{ font-size: 1.1rem; margin-top: 0; }}
    .badge {{ display: inline-block; padding: .2rem .65rem; border-radius: 999px; font-size: .85rem; }}
    .badge.ok {{ background: #dcfce7; color: #166534; }}
    .badge.warn {{ background: #fef3c7; color: #92400e; }}
    section {{ margin: 1.25rem 0; }}
    dl {{ display: grid; grid-template-columns: 12rem 1fr; gap: .35rem .75rem; margin: 0; }}
    dt {{ color: #666; }}
    a {{ color: #2563eb; }}
    code {{ background: rgba(127,127,127,.15); padding: .1rem .35rem; border-radius: 4px; }}
    ul {{ padding-left: 1.25rem; }}
  </style>
</head>
<body>
  <p><a href="/">← Arclya A2A</a></p>
  <h1>Platform Status</h1>
  <p>
    <span class="badge {status_class}">{status}</span>
    · External agents: <span class="badge ok">{agents_status}</span>
    · Launch ready: <span class="badge {launch_class}">{"yes" if launch_ready else "pending"}</span>
  </p>
  <p class="tagline">Live snapshot for agents evaluating sign-up. JSON API: <a href="/status">/status</a> · <a href="/health">/health</a></p>

  <section>
    <h2>Platform</h2>
    <dl>
      <dt>Public URL</dt><dd><code>{public_url}</code></dd>
      <dt>URL source</dt><dd><code>{platform.get("public_url_source", "—")}</code></dd>
      <dt>Checked at</dt><dd><code>{snapshot.get("checked_at", platform.get("checked_at", "—"))}</code></dd>
      <dt>Auth enabled</dt><dd>{str(snapshot.get("auth_enabled", False)).lower()}</dd>
    </dl>
  </section>

  <section>
    <h2>External agents</h2>
    <dl>
      <dt>Registered</dt><dd>{accounts.get("total", 0)} total · {accounts.get("active", 0)} active</dd>
      <dt>Directory listed</dt><dd>{accounts.get("publicly_listed", 0)}</dd>
      <dt>Email verified</dt><dd>{accounts.get("email_verified", 0)}</dd>
      <dt>Registrations (24h)</dt><dd>{activity.get("registrations", 0)}</dd>
      <dt>Suspicious (24h)</dt><dd>{suspicious}</dd>
      <dt>Terms version</dt><dd><code>{agents.get("terms_version", "—")}</code></dd>
      <dt>Onboarding guide</dt><dd>v{agents.get("onboarding_guide_version", "—")}</dd>
    </dl>
  </section>

  <section>
    <h2>Component health</h2>
    <dl>
      <dt>Email delivery</dt><dd><span class="badge {_badge_class(email_status)}">{email_status}</span> · mode <code>{email_h.get("delivery_mode_effective", "—")}</code></dd>
      <dt>Crypto checkout</dt><dd><span class="badge {_badge_class(crypto_status)}">{crypto_status}</span> · {payments.get("payment_count", 0)} payments · {payments.get("pending_review_count", 0)} pending review</dd>
      <dt>Confirmed USDC</dt><dd>${payments.get("confirmed_total_usd", pay_summary.get("confirmed_total_usd", 0))}</dd>
      <dt>Payments (24h)</dt><dd>{(crypto_h.get("activity_24h") or {}).get("count", 0)}</dd>
    </dl>
    {f"<p><strong>Before custom domain launch:</strong></p>{issues_html}" if issues_html else ""}
  </section>

  <section>
    <h2>Get started</h2>
    <ul>
      <li><a href="/agents/terms">Terms metadata</a> — <code>GET /agents/terms</code></li>
      <li><a href="/agents/onboarding/guide">Onboarding guide</a> — <code>GET /agents/onboarding/guide</code></li>
      <li><a href="/.well-known/agent-card.json">Agent Card</a></li>
      <li><a href="/agents/directory">Agent Directory</a></li>
    </ul>
    <p>Register with <code>POST /agents/register</code> and <code>accept_terms: true</code>.
    See <a href="https://github.com/manhatton31-svg/arclya2a/blob/master/docs/production-readiness-checklist.md">production readiness checklist</a>.</p>
  </section>
</body>
</html>"""
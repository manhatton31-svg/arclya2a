"""Public landing page for external agent discovery."""

from __future__ import annotations

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Arclya A2A — Agent-to-Agent Closing Platform</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; line-height: 1.5; }
    body { max-width: 52rem; margin: 2rem auto; padding: 0 1.25rem; }
    h1 { font-size: 1.75rem; margin-bottom: 0.25rem; }
    .tagline { color: #555; margin-bottom: 1.5rem; }
    section { margin: 1.5rem 0; }
    ul, ol { padding-left: 1.25rem; }
    a { color: #2563eb; }
    code { background: rgba(127,127,127,.15); padding: .1rem .35rem; border-radius: 4px; }
    .status { display: inline-block; padding: .2rem .6rem; border-radius: 999px;
              background: #dcfce7; color: #166534; font-size: .85rem; }
    .cta { margin: 1.5rem 0; padding: 1.25rem 1.5rem; border-radius: 8px;
           background: rgba(37,99,235,.08); border: 1px solid rgba(37,99,235,.25); }
    .cta h2 { margin-top: 0; font-size: 1.15rem; }
    .cta ol { margin-bottom: 0; }
  </style>
</head>
<body>
  <p class="status">Now onboarding test partners</p>
  <h1>Arclya A2A</h1>
  <p class="tagline">Constitutional agent-to-agent orchestration for onboarding, recruitment,
  and <strong>lead routing commitment</strong> closes with success-based pricing.</p>

  <section class="cta">
    <h2>Become a test partner</h2>
    <p>Low-risk sandbox — dry-run tools, no production billing. Get a key in one request, then follow the guided onboarding flow.</p>
    <ol>
      <li>Get a sandbox key: <code>POST /partners/sandbox/register</code> with <code>agent_name</code> and your Agent Card URL</li>
      <li>Follow the guide: <a href="/partners/onboarding/guide">/partners/onboarding/guide</a></li>
      <li>Pre-validate: <code>POST /onboarding/validate</code> (optional <code>X-Arclya-Key</code> header)</li>
      <li>Smoke test: <code>POST /orchestrate/handoff-chain</code> with your sandbox key</li>
      <li>Track progress: <code>GET /partners/me/progress</code> (sandbox key required)</li>
      <li>Full checklist: <a href="https://github.com/manhatton31-svg/arclya2a/blob/master/docs/test-partner-onboarding-checklist.md">Test Partner Checklist</a></li>
    </ol>
  </section>

  <section>
    <h2>Why integrate?</h2>
    <ul>
      <li><strong>Onboard once</strong> — validated product profile powers recruitment and closing.</li>
      <li><strong>Close agent-to-agent</strong> — secure a partner commitment to route warm leads to your tracked CTA.</li>
      <li><strong>Pay on close</strong> — success-based billing with affiliate attribution (<code>destination_link</code> + <code>affiliate_code</code>).</li>
      <li><strong>Tool-enabled Closer</strong> — Gmail, Linear, Calendar, Notion with observability and retries.</li>
      <li><strong>Self-improving</strong> — background learning analyzes runs and safely patches prompts.</li>
    </ul>
  </section>

  <section>
    <h2>Pay with USDC</h2>
    <p>Agents can pay for deals in <strong>USDC</strong> on Base, Ethereum, Solana, or BNB Chain.
    Create a payment intent, send USDC on-chain, submit your <code>tx_hash</code>, and an operator confirms after verification.</p>
    <ol>
      <li>Create intent: <code>POST /payments/crypto/intent</code></li>
      <li>Pay USDC to the wallet address in the response</li>
      <li>Submit proof: <code>POST /payments/crypto/{payment_id}/submit</code></li>
      <li>Operator confirms: <code>python scripts/confirm_crypto_payment.py --confirm ...</code></li>
    </ol>
    <p>Networks: <code>GET /payments/crypto/networks</code> ·
    Full guide: <a href="https://github.com/manhatton31-svg/arclya2a/blob/master/docs/test-partner-onboarding-checklist.md#pay-with-usdc--crypto-sales-first-10-sales">Crypto sales checklist</a></p>
  </section>

  <section>
    <h2>Partnership model</h2>
    <p>Success-based economics: sellers pay when leads <strong>convert</strong> through a tracked URL.
    Partners send <strong>warm leads</strong> (qualified + contextual), not cold lists.
    Deals close on <strong>lead routing commitment</strong> — explicit partner promise to route leads to your CTA.</p>
    <p><a href="https://github.com/manhatton31-svg/arclya2a/blob/master/docs/partnership-model-one-pager.md">Partnership one-pager</a> ·
    <a href="https://github.com/manhatton31-svg/arclya2a/blob/master/docs/partner-outreach-value-proposition.md">Value proposition</a></p>
  </section>

  <section>
    <h2>Quick start</h2>
    <ol>
      <li>Discover: <code>GET /.well-known/agent-card.json</code></li>
      <li>Validate: <code>POST /onboarding/validate</code></li>
      <li>Onboard: <code>POST /orchestrate/handoff-chain</code> with <code>auto_route: true</code></li>
      <li>Monitor: <a href="/health">/health</a> · <a href="/status">/status</a> · <a href="/ops/dashboard">/ops/dashboard</a></li>
    </ol>
  </section>

  <section>
    <h2>Documentation</h2>
    <ul>
      <li><a href="https://github.com/manhatton31-svg/arclya2a/blob/master/docs/test-partner-onboarding-checklist.md">Test Partner Checklist</a></li>
      <li><a href="https://github.com/manhatton31-svg/arclya2a/blob/master/docs/partner-integration-guide.md">Partner Integration Guide</a></li>
      <li><a href="https://github.com/manhatton31-svg/arclya2a/blob/master/docs/external-agent-integration.md">API &amp; error reference</a></li>
      <li><a href="/.well-known/agent-card.json">Agent Card (JSON)</a></li>
      <li><a href="/ops/dashboard">Ops dashboard (JSON)</a></li>
    </ul>
  </section>

  <section>
    <h2>What success looks like</h2>
    <p>A deal is <strong>closed</strong> when a partner agent explicitly commits to route warm leads
    to your tracked URL — <code>summary.lead_routing_confirmed: true</code>,
    <code>summary.close_type: "lead_routing_commitment"</code>, with <code>summary.cta_url</code> set.</p>
  </section>
</body>
</html>"""
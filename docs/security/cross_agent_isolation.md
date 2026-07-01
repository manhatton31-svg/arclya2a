# Cross-Agent Isolation

Arclya separates external partner agents so one bad actor cannot poison shared learning, trust scores, or production prompts.

## What is protected

| Asset | Isolation mechanism |
|-------|---------------------|
| **Partner trust scores** (`behavior_score`, `suspicious_flags`) | Stored per `partner_id` in `data/test_partners/registry.jsonl`; events applied only to matching partner |
| **Graduation eligibility** | Per-partner milestones; Partner A EMERGENCY_STOP does not change Partner B criteria |
| **Learning signals** | Tagged with `partner_id`, `seller_agent_id`, `isolation_scope`; filtered before Meta Optimizer merge |
| **Shared prompts** (Closer, Onboarding, Recruiter) | Broad patches require `isolation.allows_global_patch` and multi-actor evidence |
| **Injection patterns** | Treated as broad-impact; blocked when signal is sandbox-only or single-partner attributed |
| **Sandbox traffic** | Scoped `sandbox`; never promotes sandbox-only issues to production-global patches |

## Isolation scopes

- **`sandbox`** — test partners (`tp_*` IDs, sandbox API keys). Stricter isolation from production.
- **`production`** — authenticated production partners with attributed incidents.
- **`platform`** — unattributed or multi-actor platform-wide signals (tool gate trends, cross-partner injection patterns).

## Learning signal rules

1. **Sandbox-only issues** (`sandbox_suspicious_partner`, `sandbox_repeat_offender`, `sandbox_tool_block`) are removed from global `issues_detected` and kept in `isolation.sandbox_isolated_issues`.
2. **Partner-scoped issues** (`high_risk_partner`) require **2+ unrelated partners** (configurable via `ARCLYA_ISOLATION_MIN_ACTORS`, default `2`) before entering global patch generation.
3. **Per-partner breakdown** is preserved in `by_partner` for operator review without affecting unrelated agents.
4. Security and execution analyzers call `apply_learning_signal_isolation()` before persisting signals.

## Patch application rules

Before a broad-impact patch is auto-applied or manually applied:

1. `check_patch_isolation()` verifies the patch's issue type and signal isolation metadata.
2. Patches that fail isolation are stored with `status: isolation_blocked` and listed in `isolation_blocked` apply results.
3. `should_auto_apply()` rejects isolation-blocked patches even when low-risk.

Broad-impact targets:

- `prompts/closer_prompt.md`
- `prompts/onboarding_prompt.md`
- `prompts/recruiter_prompt.md`
- `prompts/outreach_worker.md`
- `learning/injection_patterns.json`

## Orchestrator integration

Each handoff chain enriches agent context with:

```json
{
  "partner_id": "tp_abc123",
  "sandbox_mode": true,
  "seller_agent_id": "Demo Seller Agent",
  "isolation_scope": "sandbox"
}
```

Audit records include `partner_id`, `isolation_scope`, and `sandbox_mode` for traceability.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `ARCLYA_ISOLATION_MIN_ACTORS` | `2` | Minimum distinct partners before partner-scoped issues drive global patches |

## Current limitations

- **Shared platform prompts** — Closer/Recruiter prompts are still global files; isolation prevents *unfair* patches but cannot yet produce per-partner prompt variants.
- **Unattributed incidents** — injection scans without `partner_id` may still contribute to platform-global signals (by design for unknown attackers).
- **Internal agents** — execution signals (tool failures) reflect platform agent behavior, not external partner identity; these remain platform-scoped.
- **Manual patch apply** — operators can still force-apply blocked patches out-of-band by editing files directly (not via API guard).
- **Cross-deal SSOT** — each deal has isolated SSOT during orchestration; persistent seller profiles in `config/profiles/` are per seller agent, not per partner.

## Module reference

- `src/arclya2a/security/cross_agent_isolation.py` — core isolation logic
- `src/arclya2a/security/security_analyzer.py` — tags incidents, applies signal isolation
- `src/arclya2a/learning/patch_generator.py` — isolation check before apply
- `src/arclya2a/learning/prompt_updater.py` — filters patches at generation time
- `src/arclya2a/orchestrator/engine.py` — context enrichment per partner
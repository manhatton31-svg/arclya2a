"""Request and response models for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


VALID_ENTRY_AGENTS = frozenset({
    "onboarding_specialist",
    "recruiter",
    "closer",
    "outreach_worker",
    "meta_optimizer",
})

VALID_ACQUISITION_STAGES = frozenset({"prospect", "invited", "recruiting", "qualified", "no_match"})


class HandoffChainRequest(BaseModel):
    """Payload for POST /orchestrate/handoff-chain."""

    deal_id: str = Field(..., min_length=1, max_length=128, description="Unique deal or session identifier")
    customer_company: str = Field(default="Acme Corp", min_length=1, max_length=256)
    task_context: str = Field(
        default="Execute orchestrated agent handoff chain",
        min_length=1,
        max_length=8000,
        description="Agent-to-agent task instruction for this orchestration run",
    )
    revenue_usd: float = Field(default=49.0, gt=0, le=1_000_000)
    estimated_cost_usd: float = Field(default=5.0, ge=0, le=1_000_000)
    auto_route: bool = Field(default=True, description="When true, entry agent is chosen from SSOT state")
    entry_agent: str | None = Field(default=None, description="Explicit entry agent override")
    onboarding_complete: bool = False
    product_profile_complete: bool = False
    lead_warmth: Literal["cold", "warm"] = "cold"
    acquisition_stage: str | None = Field(
        default=None,
        description="Recruiter routing hint: prospect | invited | recruiting | qualified",
    )
    product_profile: dict[str, Any] | None = Field(
        default=None,
        description="Seller product profile for onboarded agents",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Additional SSOT metadata merged into the orchestration context",
    )
    initial_ssot: dict[str, Any] | None = Field(
        default=None,
        description="Optional full SSOT override for advanced external agents",
    )

    @field_validator("entry_agent")
    @classmethod
    def validate_entry_agent(cls, value: str | None) -> str | None:
        if value is not None and value not in VALID_ENTRY_AGENTS:
            raise ValueError(
                f"entry_agent must be one of: {', '.join(sorted(VALID_ENTRY_AGENTS))}"
            )
        return value

    @field_validator("acquisition_stage")
    @classmethod
    def validate_acquisition_stage(cls, value: str | None) -> str | None:
        if value is not None and value not in VALID_ACQUISITION_STAGES:
            raise ValueError(
                f"acquisition_stage must be one of: {', '.join(sorted(VALID_ACQUISITION_STAGES))}"
            )
        return value


class HandoffChainSummary(BaseModel):
    """High-level outcome summary returned with handoff-chain responses."""

    entry_agent: str | None = None
    agents_executed: list[str] = Field(default_factory=list)
    emergency_stop: bool = False
    onboarding_complete: bool | None = None
    profile_saved: bool = False
    destination_cta: str | None = None
    acquisition_stage: str | None = None
    deal_closed: bool | None = None
    lead_routing_confirmed: bool | None = None
    close_type: str | None = None
    cta_url: str | None = None
    margin_approved: bool | None = None
    qc_passed: bool | None = None


class HandoffChainResponse(BaseModel):
    """Structured success response for POST /orchestrate/handoff-chain."""

    entry_agent: str | None = None
    summary: HandoffChainSummary
    handoff_chain: list[dict[str, Any]] = Field(default_factory=list)
    final_ssot: dict[str, Any] = Field(default_factory=dict)
    audit_ids: list[str] = Field(default_factory=list)
    cost_records: list[dict[str, Any]] = Field(default_factory=list)
    emergency_stop: bool = False
    uses_xai_inference: bool = False
    sandbox_mode: bool | None = None
    test_marker: str | None = None
    tools_mode: str | None = None


class CryptoPaymentIntentRequest(BaseModel):
    """Payload for POST /payments/crypto/intent."""

    amount: float = Field(..., gt=0, le=1_000_000, description="USD amount to collect in USDC")
    network: str | None = Field(
        default=None,
        description="base | ethereum | solana | bnb (defaults to server config)",
    )
    partner_id: str | None = Field(default=None, max_length=128)
    deal_id: str | None = Field(default=None, max_length=128)
    agent_id: str | None = Field(default=None, max_length=128)
    customer_ref: str | None = Field(default=None, max_length=256)
    memo: str | None = Field(default=None, max_length=256)
    package: str | None = Field(
        default=None,
        max_length=64,
        description="Optional package id for service attribution (onboarding_package, closer_access, per_close)",
    )


class CryptoPaymentCheckoutRequest(BaseModel):
    """Payload for POST /payments/crypto/checkout — package-based self-service checkout."""

    package: str | None = Field(
        default=None,
        max_length=64,
        description="Package id: onboarding_package | closer_access | per_close",
    )
    service_type: str | None = Field(
        default=None,
        max_length=64,
        description="Alias for package (onboarding, closer, per_close)",
    )
    network: str | None = Field(
        default=None,
        description="base | ethereum | solana | bnb (defaults to server config; Base recommended)",
    )
    partner_id: str | None = Field(default=None, max_length=128)
    deal_id: str | None = Field(default=None, max_length=128)
    agent_id: str | None = Field(default=None, max_length=128)
    customer_ref: str | None = Field(default=None, max_length=256)


class CryptoPaymentSubmitRequest(BaseModel):
    """Payload for POST /payments/crypto/{payment_id}/submit."""

    tx_hash: str = Field(..., min_length=8, max_length=256, description="On-chain transaction hash")
    network: str | None = Field(default=None, description="Optional network confirmation")


class CryptoPaymentConfirmRequest(BaseModel):
    """Payload for operator POST /payments/crypto/{payment_id}/confirm."""

    tx_hash: str | None = Field(default=None, max_length=256, description="Verified on-chain tx hash")
    confirmed_by: str | None = Field(default=None, max_length=128, description="Operator identifier")
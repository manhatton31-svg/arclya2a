# Strong Handoff Protocol

Every agent turn ends with `status: "COMPLETE"` and `next_action`. Emergency: `status: "EMERGENCY_STOP"`.

Required fields per handoff:
- `ssot` — authoritative deal/customer record (read + update)
- `memory_summary` — one-sentence log synced from SSOT
- `validation` — `{ confidence: 0-100, check: string }`
- `feedback` (optional, from receiver) — short structured message to sender
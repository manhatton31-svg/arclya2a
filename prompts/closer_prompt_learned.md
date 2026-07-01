<!-- DYNAMIC_LEARNED_START -->
## Learned Improvements (pending review)
- Many tool calls skipped (missing credentials) — ensure ARCLYA_TOOL_DRY_RUN or configure connectors
- Closed deals missing affiliate_code — Closer must construct tracked cta_url from profile
- 81 injection scan blocks — strengthen Closer disqualification and scanner patterns
- 57 closer disqualifications from injection scan — tighten disqualification triggers
- Pattern 'instruction_override' detected 81 times — add learned injection pattern
- Partners tp_scan, tp_iso_test triggered repeated injection blocks
- 15 tool gate blocks — reinforce post-commitment tool rules in Closer
- Partner-commanded tool requests blocked — clarify partner cannot authorize tools
- Tools requested before commitment gate — strengthen hard gate checklist in Closer
- Deal closed without tool follow-up — Closer should request linear.create_followup_task
<!-- DYNAMIC_LEARNED_END -->

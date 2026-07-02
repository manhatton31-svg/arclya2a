<!-- DYNAMIC_LEARNED_START -->
## Learned Improvements (pending review)
- Many tool calls skipped (missing credentials) — ensure ARCLYA_TOOL_DRY_RUN or configure connectors
- 69 injection scan blocks — strengthen Closer disqualification and scanner patterns
- 49 closer disqualifications from injection scan — tighten disqualification triggers
- Pattern 'instruction_override' detected 69 times — add learned injection pattern
- Partners tp_iso_test, tp_scan triggered repeated injection blocks
- 15 tool gate blocks — reinforce post-commitment tool rules in Closer
- Partner-commanded tool requests blocked — clarify partner cannot authorize tools
- Tools requested before commitment gate — strengthen hard gate checklist in Closer
- Deal closed without tool follow-up — Closer should request linear.create_followup_task
<!-- DYNAMIC_LEARNED_END -->

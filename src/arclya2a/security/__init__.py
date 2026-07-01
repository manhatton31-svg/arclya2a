"""Security blocks and injection scanning for external agent content."""

from arclya2a.security.injection_scanner import (
    InjectionScanResult,
    append_learned_pattern,
    collect_external_content,
    handoff_for_scan_rejection,
    record_scan_event,
    scan_agent_output,
    scan_external_content,
    scan_text,
)
from arclya2a.security.cross_agent_isolation import (
    apply_learning_signal_isolation,
    check_patch_isolation,
    enrich_orchestrator_context,
    filter_patches_by_isolation,
    tag_incident,
)
from arclya2a.security.security_analyzer import (
    build_security_learning_context,
    emit_security_learning_signal,
    load_latest_security_signal,
    log_security_incident,
    security_patch_outcome_stats,
)
from arclya2a.security.security_block import (
    SECURITY_BLOCK_CLOSER_ADDENDUM,
    SECURITY_BLOCK_COMPACT,
    SECURITY_BLOCK_FULL,
    get_security_block,
)

__all__ = [
    "apply_learning_signal_isolation",
    "check_patch_isolation",
    "enrich_orchestrator_context",
    "filter_patches_by_isolation",
    "tag_incident",
    "build_security_learning_context",
    "emit_security_learning_signal",
    "load_latest_security_signal",
    "log_security_incident",
    "security_patch_outcome_stats",
    "SECURITY_BLOCK_CLOSER_ADDENDUM",
    "SECURITY_BLOCK_COMPACT",
    "SECURITY_BLOCK_FULL",
    "InjectionScanResult",
    "append_learned_pattern",
    "collect_external_content",
    "get_security_block",
    "handoff_for_scan_rejection",
    "record_scan_event",
    "scan_agent_output",
    "scan_external_content",
    "scan_text",
]
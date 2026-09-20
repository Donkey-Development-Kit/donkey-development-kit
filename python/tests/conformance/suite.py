"""The adapter conformance kit — the most important test asset.

ONE suite, defined once. Its blocking scope is the conformance-tested roster
(BG §1.8, #197): LangGraph and the raw client. A framework is "supported" only
when it passes all of it, or records a documented, asserted exemption in
``KNOWN_LIMITATIONS`` (the conformance kit) — never a silent skip. The other seven frameworks
are supported at ``connection_kwargs()`` only and are not run here; a demoted
framework rejoins with its own conformance run when demand promotes it
(#223/#244). The customer-facing pytest plugin (``donkey_kit.conformance``, #191)
is the shipped deliverable this internal matrix backstops.

The scenario bodies are wired in M1+ against captured contract fixtures (BG §1.5)
and the local gateway (BG §1.4). This module fixes the scenario list and the
exemption table now so the kit exists before the second adapter is built
(working instruction #5).
"""

from __future__ import annotations

CONFORMANCE_SCENARIOS = [
    "simple_completion",
    "streaming_completion",
    "single_tool_call",
    "multi_tool_multi_server",
    "tool_filtering",
    "policy_violation_terminal",
    "attribution_headers_present",
    "correlation_id_propagated",
    "governed_filter_excludes",
    "governance_resolve_drift",
    "governance_target_switch",
    "descriptor_auto_stable",
    "publication_verify_drift",
    "publication_idempotent",
    "descriptor_matches_framework",
    "auto_vs_live_agree",
    "dynamic_tools_detected",
    "asset_type_detection",
    # After a governed 200, donkey.last_call carries the gateway's own identity
    # (request_id / api_instance_id / environment_id) for the call just made
    # (BG §1.1, #362). Mirrors correlation_id_propagated: the adapters that route
    # outside our transport cannot observe it and record an asserted exemption
    # below rather than a bare None.
    "gateway_identity_observed",
]

# Documented, ASSERTED exemptions — published in the README (the conformance kit). A framework
# that cannot satisfy a scenario records WHY here rather than skipping silently.
_LITELLM_TRANSPORT_EXEMPTION = (
    "LiteLLM owns the transport; we cannot inject our httpx client, so the "
    "correlation ID is per-client, not per-run (BG §1.8). A LiteLLM custom "
    "logger callback may later recover trace correlation."
)

# gateway_identity_observed has the SAME structural cause as the correlation-id
# exemption, stated for last_call (#362): the record is populated by our
# transport's _on_response, so an adapter that does not route through our httpx
# client can never observe it. donkey.last_call reports UNAVAILABLE (naming the
# surface) rather than a bare None — the honest-state contract (hazard #3) —
# and mirrors Adapter.observes_last_call = False on each of these adapters.
_LITELLM_LAST_CALL_EXEMPTION = (
    "LiteLLM owns the transport; no response reaches our _on_response, so "
    "donkey.last_call cannot observe the gateway identity of the call and "
    "reports UNAVAILABLE (#362, same cause as correlation_id_propagated BG §1.8)."
)
_DEFAULT_HEADERS_LAST_CALL_EXEMPTION = (
    "The adapter is handed only default_headers, never our httpx client, so no "
    "response reaches our _on_response; donkey.last_call cannot observe the "
    "gateway identity and reports UNAVAILABLE (#362)."
)

KNOWN_LIMITATIONS: dict[str, dict[str, str]] = {
    # ADK and CrewAI both reach models through LiteLLM (BG §1.8).
    "adk": {
        "correlation_id_propagated": _LITELLM_TRANSPORT_EXEMPTION,
        "gateway_identity_observed": _LITELLM_LAST_CALL_EXEMPTION,
    },
    "crewai": {
        "correlation_id_propagated": _LITELLM_TRANSPORT_EXEMPTION,
        "gateway_identity_observed": _LITELLM_LAST_CALL_EXEMPTION,
    },
    # LlamaIndex and MS Agent Framework get only default_headers, no httpx client
    # (BG §1.8), so they cannot observe last_call either — but they CAN propagate the
    # correlation id through those headers, so that scenario is not exempt for them.
    "llamaindex": {"gateway_identity_observed": _DEFAULT_HEADERS_LAST_CALL_EXEMPTION},
    "agent_framework": {
        "gateway_identity_observed": _DEFAULT_HEADERS_LAST_CALL_EXEMPTION
    },
}

"""The adapter conformance kit — the most important test asset.

ONE suite, defined once. Its blocking scope is the conformance-tested roster
(BG §1.8, #197): LangGraph and the raw client. A framework is "supported" only
when it passes all of it, or records a documented, asserted exemption in
``KNOWN_LIMITATIONS`` (the conformance kit) — never a silent skip. The other seven frameworks
are supported at ``connection_kwargs()`` only and are not run here; a demoted
framework rejoins with its own conformance run when demand promotes it
(#223/#244). The customer-facing pytest plugin (``donkey_kit.conformance``, #191)
is the shipped deliverable this internal matrix backstops.

The scenario bodies are wired against captured contract fixtures (BG §1.5) and
the local gateway (BG §1.4) as their gating features land (#444). This module
fixes the scenario list and the exemption table now so the kit exists before
the second adapter is built (working instruction #5).
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
    # In jwt / model-wallet auth mode (#509) the rotating JWT is injected per-send
    # from the attached AuthProvider, so an expired token is refreshed on the next
    # request (and on a 401, via the transport's invalidate→retry-once loop).
    # Holds only for adapters routed through our httpx client; the frameworks that
    # build their own transport from a one-time default_headers snapshot pin the
    # token at construction and record an asserted exemption below (BG §1.8).
    "jwt_token_refreshed",
]

# Documented, ASSERTED exemptions — published in the README (the conformance kit). A framework
# that cannot satisfy a scenario records WHY here rather than skipping silently.
_LITELLM_TRANSPORT_EXEMPTION = (
    "LiteLLM owns the transport; we cannot inject our httpx client, so the "
    "correlation ID is per-client, not per-run (BG §1.8). A LiteLLM custom "
    "logger callback may later recover trace correlation."
)
_DEFAULT_HEADERS_CORRELATION_EXEMPTION = (
    "The adapter is handed only a static default_headers snapshot, which "
    "deliberately excludes the per-run correlation ID; without our httpx "
    "client, donkey.run(id=...) cannot update the request headers (BG §1.8)."
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

# jwt_token_refreshed has the SAME structural cause as the correlation-id and
# last-call exemptions (#509): a rotating model-wallet JWT can only be refreshed
# per-send by our transport. A framework that owns its transport (LiteLLM) or is
# handed only a one-time default_headers snapshot pins whatever token existed at
# construction and starts 401-ing after it expires — so jwt auth mode is
# unsupported on those adapters, asserted here rather than silently skipped.
_LITELLM_JWT_EXEMPTION = (
    "LiteLLM owns the transport; we cannot inject our httpx client, so a rotating "
    "model-wallet JWT cannot be refreshed per-send and would expire. Use client-id "
    "auth with this adapter, or route the raw/LangGraph client for jwt mode (#509)."
)
_DEFAULT_HEADERS_JWT_EXEMPTION = (
    "The adapter is handed only a static default_headers snapshot, which pins the "
    "JWT at construction; without our httpx client the token cannot be refreshed "
    "and 401s after expiry. jwt auth mode is async-only through our transport (#509)."
)

KNOWN_LIMITATIONS: dict[str, dict[str, str]] = {
    # ADK and CrewAI both reach models through LiteLLM (BG §1.8).
    "adk": {
        "correlation_id_propagated": _LITELLM_TRANSPORT_EXEMPTION,
        "gateway_identity_observed": _LITELLM_LAST_CALL_EXEMPTION,
        "jwt_token_refreshed": _LITELLM_JWT_EXEMPTION,
    },
    "crewai": {
        "correlation_id_propagated": _LITELLM_TRANSPORT_EXEMPTION,
        "gateway_identity_observed": _LITELLM_LAST_CALL_EXEMPTION,
        "jwt_token_refreshed": _LITELLM_JWT_EXEMPTION,
    },
    # LlamaIndex and MS Agent Framework get only a static default_headers snapshot,
    # no httpx client (BG §1.8). The snapshot deliberately excludes the per-run
    # correlation ID, cannot observe last_call responses, and cannot carry a
    # rotating JWT (it pins the token at construction).
    "llamaindex": {
        "correlation_id_propagated": _DEFAULT_HEADERS_CORRELATION_EXEMPTION,
        "gateway_identity_observed": _DEFAULT_HEADERS_LAST_CALL_EXEMPTION,
        "jwt_token_refreshed": _DEFAULT_HEADERS_JWT_EXEMPTION,
    },
    "agent_framework": {
        "correlation_id_propagated": _DEFAULT_HEADERS_CORRELATION_EXEMPTION,
        "gateway_identity_observed": _DEFAULT_HEADERS_LAST_CALL_EXEMPTION,
        "jwt_token_refreshed": _DEFAULT_HEADERS_JWT_EXEMPTION,
    },
}

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
    # Holds only for adapters routed through our httpx client. CrewAI's provider
    # builds its own transport, never sees the JWT, and refuses jwt mode with a
    # ConfigError; it records an asserted exemption below (BG §1.8, #828).
    "jwt_token_refreshed",
]

# Documented, ASSERTED exemptions — published in the README (the conformance kit). A framework
# that cannot satisfy a scenario records WHY here rather than skipping silently.
_LITELLM_TRANSPORT_EXEMPTION = (
    "adk.model() only: LiteLLM owns the transport; we cannot inject our httpx client, so the "
    "correlation ID is per-client, not per-run (BG §1.8). A LiteLLM custom "
    "logger callback may later recover trace correlation."
)
_CREWAI_TRANSPORT_EXEMPTION = (
    "CrewAI's native OpenAI provider owns the transport: it builds its own sync and "
    "async OpenAI clients from one set of client params, so our async httpx client "
    "cannot be injected and the correlation ID is per-client, not per-run (BG §1.8)."
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
    "adk.model() only: LiteLLM owns the transport; no response reaches our _on_response, so "
    "donkey.last_call cannot observe the gateway identity of the call and "
    "reports UNAVAILABLE (#362, same cause as correlation_id_propagated BG §1.8)."
)
_CREWAI_LAST_CALL_EXEMPTION = (
    "CrewAI's native OpenAI provider owns the transport; no response reaches our "
    "_on_response, so donkey.last_call cannot observe the gateway identity of the "
    "call and reports UNAVAILABLE (#362, same cause as correlation_id_propagated BG §1.8)."
)
_DEFAULT_HEADERS_LAST_CALL_EXEMPTION = (
    "The adapter is handed only default_headers, never our httpx client, so no "
    "response reaches our _on_response; donkey.last_call cannot observe the "
    "gateway identity and reports UNAVAILABLE (#362)."
)

# jwt_token_refreshed: a rotating model-wallet JWT is added per-send only by our
# transport (#509). Every adapter that sends through it carries the JWT; CrewAI's
# native OpenAI provider builds its own clients, so it would send the api-key
# placeholder as the bearer. The adapter refuses jwt mode with a ConfigError
# instead (#828) — asserted here rather than silently skipped.
_CREWAI_JWT_EXEMPTION = (
    "CrewAI's native OpenAI provider owns the transport, so the rotating model-wallet "
    "JWT, which only our httpx client adds per-send, never reaches its requests. "
    "donkey.crewai raises ConfigError in jwt auth mode; use client-id auth with this "
    "adapter, or a transport-injected adapter for jwt mode (#509, #828)."
)

KNOWN_LIMITATIONS: dict[str, dict[str, str]] = {
    # ADK's model() reaches models through LiteLLM and CrewAI through its native
    # OpenAI provider; either way the framework owns the transport (BG §1.8).
    # adk.gemini() is handed our httpx client and records none of these (#691).
    "adk": {
        "correlation_id_propagated": _LITELLM_TRANSPORT_EXEMPTION,
        "gateway_identity_observed": _LITELLM_LAST_CALL_EXEMPTION,
    },
    "crewai": {
        "correlation_id_propagated": _CREWAI_TRANSPORT_EXEMPTION,
        "gateway_identity_observed": _CREWAI_LAST_CALL_EXEMPTION,
        "jwt_token_refreshed": _CREWAI_JWT_EXEMPTION,
    },
    # LlamaIndex and MS Agent Framework now send through our httpx client, so they
    # carry the jwt-mode JWT per-send and record no jwt_token_refreshed exemption
    # (#828). Their correlation-id and last-call rows are #740's to retire.
    "llamaindex": {
        "correlation_id_propagated": _DEFAULT_HEADERS_CORRELATION_EXEMPTION,
        "gateway_identity_observed": _DEFAULT_HEADERS_LAST_CALL_EXEMPTION,
    },
    "agent_framework": {
        "correlation_id_propagated": _DEFAULT_HEADERS_CORRELATION_EXEMPTION,
        "gateway_identity_observed": _DEFAULT_HEADERS_LAST_CALL_EXEMPTION,
    },
}

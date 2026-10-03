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
# Every adapter but CrewAI sends through our shared httpx client (#740), so only
# CrewAI is listed. Its structural reason: the native OpenAI provider builds both
# its sync OpenAI and its AsyncOpenAI from ONE client_params dict, and with an
# interceptor set it overwrites http_client with a fresh httpx client, so no
# single value can carry our async client to both (crewai 1.15, #740).
_CREWAI_TRANSPORT = (
    "CrewAI's native OpenAI provider owns the transport: one client_params dict "
    "feeds both its sync OpenAI and its AsyncOpenAI, and its interceptor path "
    "replaces http_client with its own httpx client, so our async client cannot be "
    "injected (#740)."
)
_CREWAI_TRANSPORT_EXEMPTION = (
    _CREWAI_TRANSPORT + " The correlation ID is per-client, not per-run (BG §1.8)."
)

# gateway_identity_observed has the SAME structural cause, stated for last_call
# (#362): the record is populated by our transport's _on_response, so an adapter
# that does not route through our httpx client can never observe it.
# donkey.last_call reports UNAVAILABLE (naming the surface) rather than a bare
# None — the honest-state contract (hazard #3) — and mirrors
# Adapter.observes_last_call = False.
_CREWAI_LAST_CALL_EXEMPTION = (
    _CREWAI_TRANSPORT + " No response reaches our _on_response, so donkey.last_call "
    "reports UNAVAILABLE (#362)."
)

# jwt_token_refreshed: a rotating model-wallet JWT is added per-send only by our
# transport (#509). Every adapter that sends through it carries the JWT; CrewAI
# would send the api-key placeholder as the bearer, so the adapter refuses jwt
# mode with a ConfigError instead (#828) — asserted here rather than skipped.
_CREWAI_JWT_EXEMPTION = (
    _CREWAI_TRANSPORT + " The rotating model-wallet JWT, which only our httpx client "
    "adds per-send, never reaches its requests; donkey.crewai raises ConfigError in "
    "jwt auth mode (#509, #828)."
)

KNOWN_LIMITATIONS: dict[str, dict[str, str]] = {
    "crewai": {
        "correlation_id_propagated": _CREWAI_TRANSPORT_EXEMPTION,
        "gateway_identity_observed": _CREWAI_LAST_CALL_EXEMPTION,
        "jwt_token_refreshed": _CREWAI_JWT_EXEMPTION,
    },
}

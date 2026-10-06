"""The adapter conformance kit — the most important test asset.

ONE suite, defined once. Its blocking scope is the conformance-tested roster
(BG §1.8, #197): LangGraph and the raw client. A framework is "supported" only
when it passes all of it, or records a documented, asserted exemption in
``KNOWN_LIMITATIONS`` (the conformance kit) — never a silent skip. The other seven frameworks
are supported at ``connection_kwargs()`` only and are not run here; a demoted
framework rejoins with its own conformance run when demand promotes it
(#223/#244). The customer-facing pytest plugin (``donkey_kit.conformance``, #191)
is the shipped deliverable this internal matrix backstops.

Every name below has an executable body (#749). ``SCENARIO_BODIES`` maps each
one to the tests that run it, and ``test_transport_exemptions.py`` checks that
the two lists match and that every listed test exists, so a name cannot outlive
its body. Most bodies are in the adapter contract suite
(``test_adapter_contract.py``, #742), which drives each real framework against a
loopback gateway. ``attribution_headers_present`` asserts the consumer-auth
``client_id`` / ``client_secret`` headers. The ``client_id`` is the verified
per-agent attribution unit. The application and business-group header names
are still UNVERIFIED placeholders (docs/verified-apis.md §3).

Thirteen names were **retired, not implemented** (#749): ``single_tool_call``,
``multi_tool_multi_server``, ``tool_filtering``, ``governed_filter_excludes``,
``governance_resolve_drift``, ``governance_target_switch``,
``descriptor_auto_stable``, ``publication_verify_drift``,
``publication_idempotent``, ``descriptor_matches_framework``,
``auto_vs_live_agree``, ``dynamic_tools_detected`` and ``asset_type_detection``.
Each one needs a per-adapter surface that still raises ``_verify.blocked(...)``
under the verification discipline. Binding MCP tools into a framework is
blocked in ``tools/session.py``. Exchange discovery is blocked in
``registry/exchange.py``, publication in ``registry/publication.py`` and live
introspection in ``registry/introspect.py``. No adapter can run a scenario
against a surface that always refuses. The pure logic beneath them
(``ToolFilter``, collision prefixes, governance criteria, content digests) is
unit-tested in ``tests/unit/test_pure_logic.py``. A retired name comes back
only with a body, once its surface is verified and built.
"""

from __future__ import annotations

CONFORMANCE_SCENARIOS = [
    "simple_completion",
    "streaming_completion",
    "policy_violation_terminal",
    "attribution_headers_present",
    "correlation_id_propagated",
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
    # Inside donkey.run() and @donkey.governed, a refusal or transport failure
    # reaches user code as its typed DonkeyError, not the framework's generic
    # connection or status error (BG §1.2, #724, ADR 0002). The bridge only types
    # what the SDK's transport sent or raised, so the adapters whose framework
    # owns the transport record an asserted exemption below.
    "typed_refusal_bridged",
]

# Where each scenario runs (#749), as pytest node ids relative to ``python/``.
# test_transport_exemptions.py keeps the keys equal to CONFORMANCE_SCENARIOS and
# checks that every node id names a test function that exists.
_CONTRACT = "tests/conformance/test_adapter_contract.py"
SCENARIO_BODIES: dict[str, tuple[str, ...]] = {
    "simple_completion": (
        f"{_CONTRACT}::test_call_sends_governed_headers_and_correlation",
        f"{_CONTRACT}::test_sync_call",
    ),
    "streaming_completion": (f"{_CONTRACT}::test_streamed_call",),
    "policy_violation_terminal": (f"{_CONTRACT}::test_refusal_is_sent_once_and_typed",),
    "attribution_headers_present": (
        f"{_CONTRACT}::test_call_sends_governed_headers_and_correlation",
    ),
    "correlation_id_propagated": (
        f"{_CONTRACT}::test_call_sends_governed_headers_and_correlation",
    ),
    "gateway_identity_observed": (
        f"{_CONTRACT}::test_last_call_matches_the_declared_metadata",
        "tests/conformance/test_transport_exemptions.py::test_exemption_matches_observes_last_call_flag",
    ),
    "jwt_token_refreshed": (
        "tests/unit/test_adapter_jwt_mode.py::test_async_call_carries_the_jwt",
        "tests/unit/test_adapter_jwt_mode.py::test_crewai_refuses_jwt_mode_before_sending",
    ),
    "typed_refusal_bridged": (
        "tests/conformance/test_typed_refusal_bridge.py::test_refusal_reaches_user_code_typed",
    ),
}

# Documented, ASSERTED exemptions — published in the README (the conformance kit). A framework
# that cannot satisfy a scenario records WHY here rather than skipping silently.
# Every adapter but CrewAI sends through our shared httpx client (#740), so
# CrewAI is the only one listed for the transport scenarios; ADK's model() is
# listed for typed_refusal_bridged alone (#724). CrewAI's structural reason: the
# native OpenAI provider builds both its sync OpenAI and its AsyncOpenAI from
# ONE client_params dict, and the openai SDK type-checks http_client per client,
# so no single value can carry our clients to both. With an interceptor set it
# overwrites http_client with a fresh httpx client whose transport always sends
# itself, and the interceptor's hooks only edit the request and response (crewai
# 1.15.3 to 1.15.23, #740, #958; docs/verified-apis.md §8.2).
_CREWAI_TRANSPORT = (
    "CrewAI's native OpenAI provider owns the transport: one client_params dict "
    "feeds both its sync OpenAI and its AsyncOpenAI, which type-check http_client "
    "against different classes, and its interceptor path replaces http_client "
    "with its own httpx client whose interceptor can edit the request but not "
    "reroute the send, so our clients cannot be injected (#740, #958)."
)
_CREWAI_TRANSPORT_EXEMPTION = (
    _CREWAI_TRANSPORT + " The correlation ID is per-client, not per-run (BG §1.8)."
)

# gateway_identity_observed has the SAME structural cause, stated for last_call
# (#362): the record is populated by our transport's _on_response, so an adapter
# that does not route through our httpx client can never observe it.
# donkey.last_call reports UNAVAILABLE (naming the surface) rather than a bare
# None — the honest-state contract (hazard #3) — and mirrors
# observes_last_call=False in the adapter's default-factory AdapterCapabilities
# (#726; tests/unit/test_adapter_roster.py keeps them equal).
_CREWAI_LAST_CALL_EXEMPTION = (
    _CREWAI_TRANSPORT + " No response reaches our _on_response, so donkey.last_call "
    "reports UNAVAILABLE (#362)."
)

# jwt_token_refreshed: a rotating model-wallet JWT is added per-send only by our
# transport (#509). Every adapter that sends through it carries the JWT; CrewAI
# would send the api-key placeholder as the bearer, so the adapter refuses jwt
# mode with a ConfigError instead (#828) — asserted here rather than skipped.
# Mirrors transport="framework" in its AdapterCapabilities (#726).
_CREWAI_JWT_EXEMPTION = (
    _CREWAI_TRANSPORT + " The rotating model-wallet JWT, which only our httpx client "
    "adds per-send, never reaches its requests; donkey.crewai raises ConfigError in "
    "jwt auth mode (#509, #828)."
)

# typed_refusal_bridged: the bridge types an error only when the SDK's transport
# raised it or sent the request behind it (#724, ADR 0002). A framework that owns
# the transport raises its own errors for a call the SDK never saw, and one that
# re-wraps a refusal around a response it rebuilt hides the one the SDK sent, so
# the bridge leaves both as they are rather than guess at their shape. Mirrors
# typed_refusals=False in the factory's AdapterCapabilities (#726).
_LITELLM_REFUSAL_EXEMPTION = (
    "adk.model() only: LiteLLM sends through our transport (#946) but re-raises a "
    "refusal as its own exception around a response it rebuilt, which carries no "
    "sign that our transport sent it; the typed-refusal bridge cannot tell a gateway "
    "refusal from any other failure there, so it passes them through (#724). "
    "adk.gemini() is handed our httpx client and is bridged (conformance-tested, #955)."
)
_CREWAI_REFUSAL_EXEMPTION = (
    "CrewAI's native OpenAI provider owns the transport, so its errors come from a "
    "client the SDK never saw; the typed-refusal bridge passes them through rather "
    "than classify a response our transport did not send (#724)."
)

KNOWN_LIMITATIONS: dict[str, dict[str, str]] = {
    # ADK's model() and CrewAI each raise a framework error the bridge cannot type;
    # adk.gemini() is handed our httpx client, records no exemption (#691) and is
    # bridge-tested in test_typed_refusal_bridge.py (#955).
    "adk": {"typed_refusal_bridged": _LITELLM_REFUSAL_EXEMPTION},
    "crewai": {
        "correlation_id_propagated": _CREWAI_TRANSPORT_EXEMPTION,
        "gateway_identity_observed": _CREWAI_LAST_CALL_EXEMPTION,
        "jwt_token_refreshed": _CREWAI_JWT_EXEMPTION,
        "typed_refusal_bridged": _CREWAI_REFUSAL_EXEMPTION,
    },
}

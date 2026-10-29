"""The adapter conformance kit — the most important test asset.

ONE suite, defined once. Its blocking scope is the conformance-tested roster
(BG §1.8, #197): LangGraph, the OpenAI Agents SDK (promoted by Phase 1 demand,
#223) and the raw client. A framework is "supported" only when it passes all of
it, or records a documented, asserted exemption in ``KNOWN_LIMITATIONS`` (the
conformance kit) — never a silent skip. The other six frameworks are supported
at ``connection_kwargs()`` only and are not run here, except for the three
tool-binding scenarios, which run every ``ToolSet`` binding
(``test_tool_binding.py``). A demoted framework rejoins with its own
conformance run when demand promotes it (#223/#244). The
customer-facing pytest plugin (``donkey_kit.conformance``, #191) is the shipped
deliverable this internal matrix backstops.

Every name below has an executable body (#749). ``SCENARIO_BODIES`` maps each
one to the tests that run it, and ``test_transport_exemptions.py`` checks that
the two lists match and that every listed test exists, so a name cannot outlive
its body. Most bodies are in the adapter contract suite
(``test_adapter_contract.py``, #742), which drives each real framework against a
loopback gateway. ``attribution_headers_present`` asserts the consumer-auth
``client_id`` / ``client_secret`` headers. The ``client_id`` is the verified
per-agent attribution unit. The application and business-group header names
are still UNVERIFIED placeholders (docs/verified-apis.md §3).

Thirteen names were **retired, not implemented** (#749). Every one is back
with a body, or dropped: ``governance_resolve_drift`` and
``governance_target_switch`` were dropped with the declarative control plane
(#730, ADR 0008). ``publication_idempotent`` came back with ``donkey publish``
(#218); ``single_tool_call``, ``multi_tool_multi_server`` and ``tool_filtering``
with the verified ``ToolSet`` bindings (docs/verified-apis.md §9, #991);
``governed_filter_excludes`` with ``donkey.tools.discover()`` (#221, #991); and
``descriptor_matches_framework``, ``auto_vs_live_agree``,
``asset_type_detection`` and ``dynamic_tools_detected`` with the verified
descriptor-derivation attributes (docs/verified-apis.md §10, #1024, #991).
``dynamic_tools_detected`` came back for ``auto`` only: ``auto:static`` was
dropped as UNSUPPORTED (#1248). ``descriptor_auto_stable`` came back with the
content digest over a derived descriptor, and ``publication_verify_drift`` with
``Publication.verify()`` (#991). The pure logic beneath them (``ToolFilter``,
collision prefixes, governance criteria, content digests) is also unit-tested
in ``tests/unit/test_pure_logic.py``.
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
    # A reaction registered with donkey.on(...) runs at the transport's
    # _on_refusal hook and never turns a refusal into a success: the refusal
    # propagates, or a NEW governed request goes out and its own result returns
    # (BG §2.1, #208, #209). Only adapters routed through our httpx client run
    # handlers, so CrewAI records an asserted exemption below.
    "refusal_handler_cannot_unrefuse",
    # A refusal shape registered with classify.register() reaches user code as
    # its registered type inside donkey.run(), a donkey.on(...) handler for it
    # runs, and the span names its slug verbatim (BG §2.2, #210). The bridge and
    # handlers both need our transport, so CrewAI records an asserted exemption.
    "custom_policy_typed",
    # The gateway's Agent Kill Switch refusal reaches user code as AgentKilled,
    # is sent once with retries on, runs no donkey.on(...) handler, wakes and
    # stops a sibling pace() loop, and flushes spans (BG §2.6, #220). All of it
    # happens in our transport, so CrewAI records an asserted exemption below.
    "agent_killed_stops_cleanly",
    # An approval-gated tool pauses with the framework's own primitive (the raw
    # client raises ApprovalRequired; LangGraph calls interrupt(); the OpenAI
    # Agents SDK stops on needs_approval), sends nothing while paused, records
    # the approver on resume, and its resumed model call is still judged by the
    # gateway: a refusal comes back typed, under the decision event's
    # correlation id (BG §2.3, #211, #212, #213). The resumed call needs our
    # transport, so CrewAI records an asserted exemption below.
    "approval_round_trip",
    # Publishing the same content twice creates one Exchange version: a re-run,
    # or a version bump with nothing else changed, publishes nothing, and new
    # content under a published version fails before sending (BG §2.5, #218).
    # Publishing goes through the control plane, not a framework, so no adapter
    # is exempt.
    "publication_idempotent",
    # A ToolSet bound with a framework's own MCP class (ToolSet.langgraph() ...
    # crewai()) lists the server's tools under their own names, and a call goes
    # through the framework's tool object to the server with the consumer-auth
    # pair (BG §2.7, docs/verified-apis.md §9, #221, #991). Every binding passes.
    "single_tool_call",
    # Two servers exposing the same tool names: a per-tool binding (LangGraph,
    # LlamaIndex, Anthropic) renames each clash to <server>__<tool> and routes
    # each call to its own server. The whole-server bindings cannot rename one
    # tool and record an asserted exemption below.
    "multi_tool_multi_server",
    # ToolSet.filter(allow=..., deny=...) decides what the framework itself lists:
    # a removed tool is never bound (BG §2.7, #222). Every binding passes.
    "tool_filtering",
    # The gateway's MCP tool-access denial (HTTP 200, JSON-RPC -32008, BG §2.7,
    # docs/verified-apis.md §9, #303) on a bound tool raises ToolNotInProfile
    # through a per-tool binding (#312). The whole-server bindings leave the call
    # to the framework and record an asserted exemption below.
    "tool_access_denied_typed",
    # discover(governed=True) leaves out every MCP server whose governed state
    # fails the criteria, and logs why (BG §2.7, #221, #991). Discovery reads the
    # registry, not a framework, so no adapter is exempt.
    "governed_filter_excludes",
    # derive_descriptor() reads each framework's tool exactly as the framework
    # computed it: equal to an independent read of the same attributes, and to
    # the committed capture at the pinned version (BG §2.5, docs/verified-apis.md
    # §10, #1024). Every framework's read is verified, so none is exempt.
    "descriptor_matches_framework",
    # For an MCP server, auto (read the object) and auto:live (initialize +
    # tools/list) derive the same descriptor, on both MCP SDK majors (BG §2.5,
    # #1024). Framework tools have no protocol surface, so this is about MCP
    # servers, not adapters.
    "auto_vs_live_agree",
    # derive_descriptor() tells an MCP server, framework tools, a list of them and
    # a zero-argument factory apart, and refuses a plain function or an
    # unrecognised object rather than guess a schema (BG §2.5, #1024).
    "asset_type_detection",
    # Tools built at import time (in a loop, say) are all in the auto descriptor,
    # since auto imports the module (BG §2.5). auto:static, which would have
    # warned instead, was dropped (#1248).
    "dynamic_tools_detected",
    # Two introspections of one unchanged server derive the same content digest,
    # so --if-changed never publishes a version for nothing (BG §2.5, #991).
    "descriptor_auto_stable",
    # A tool added on the live server since its version was published is drift:
    # Publication.verify() warns, or raises PublicationDrift (BG §2.5, #991). It
    # compares Exchange's mcp-metadata with tools/list, not a framework.
    "publication_verify_drift",
]

# Where each scenario runs (#749), as pytest node ids relative to ``python/``.
# test_transport_exemptions.py keeps the keys equal to CONFORMANCE_SCENARIOS and
# checks that every node id names a test function that exists.
_CONTRACT = "tests/conformance/test_adapter_contract.py"
_BINDING = "tests/conformance/test_tool_binding.py"
_DESCRIPTOR = "tests/unit/test_descriptor_derivation.py"
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
    "refusal_handler_cannot_unrefuse": (
        "tests/conformance/test_handlers_cannot_unrefuse.py::test_a_handler_never_turns_a_refusal_into_a_success",
        "tests/conformance/test_handlers_cannot_unrefuse.py::test_a_success_comes_only_from_a_new_governed_request",
    ),
    "custom_policy_typed": (
        "tests/conformance/test_custom_policy_typed.py::test_a_custom_refusal_is_typed_and_handled",
        "tests/conformance/test_custom_policy_typed.py::test_a_custom_refusal_is_named_on_the_span",
    ),
    "agent_killed_stops_cleanly": (
        "tests/conformance/test_agent_killed_stops_cleanly.py::test_a_kill_stops_the_agent_and_its_pace_loops",
    ),
    "approval_round_trip": (
        "tests/conformance/test_approval_round_trip.py::test_an_approved_tool_resumes_and_is_still_judged_by_the_gateway",
    ),
    "publication_idempotent": (
        "tests/unit/test_cli_publish.py::test_publish_then_rerun_publishes_once",
        "tests/unit/test_exchange_publish.py::test_a_version_bump_alone_publishes_nothing_unless_always",
        "tests/unit/test_exchange_publish.py::test_a_published_version_with_other_content_fails_before_sending",
    ),
    "single_tool_call": (f"{_BINDING}::test_single_tool_call",),
    "multi_tool_multi_server": (
        f"{_BINDING}::test_multi_tool_multi_server",
        f"{_BINDING}::test_the_whole_server_bindings_are_exactly_the_exempt_ones",
        "tests/unit/test_discover.py::test_a_per_tool_plan_renames_and_gates",
        "tests/unit/test_discover.py::test_a_whole_server_plan_refuses_a_rename_and_a_gate",
    ),
    "tool_filtering": (f"{_BINDING}::test_tool_filtering",),
    "tool_access_denied_typed": (
        f"{_BINDING}::test_tool_access_denied_typed",
        f"{_BINDING}::test_the_denial_exemptions_are_exactly_the_whole_server_bindings",
    ),
    "governed_filter_excludes": (
        "tests/unit/test_discover.py::test_governed_discover_leaves_out_ungoverned_with_reasons",
    ),
    "descriptor_matches_framework": (
        f"{_DESCRIPTOR}::test_derivation_equals_the_independent_attribute_read",
        f"{_DESCRIPTOR}::test_derivation_matches_the_fixture_at_the_captured_version",
    ),
    "auto_vs_live_agree": (f"{_DESCRIPTOR}::test_live_mcp_tools_list_equals_object_introspection",),
    "asset_type_detection": (
        f"{_DESCRIPTOR}::test_anthropic_tool_params_derive_in_order",
        f"{_DESCRIPTOR}::test_zero_argument_factory_is_called",
        f"{_DESCRIPTOR}::test_plain_function_is_never_re_derived",
        f"{_DESCRIPTOR}::test_unverified_framework_object_stays_blocked",
        f"{_DESCRIPTOR}::test_live_needs_an_mcp_server",
    ),
    "dynamic_tools_detected": (f"{_DESCRIPTOR}::test_tools_registered_in_a_loop_are_all_derived",),
    "descriptor_auto_stable": (
        f"{_DESCRIPTOR}::test_two_introspections_of_one_server_digest_equal",
    ),
    "publication_verify_drift": (
        "tests/unit/test_publication_verify.py::"
        "test_an_added_tool_on_the_live_server_raises_publication_drift",
        "tests/unit/test_publication_verify.py::test_an_added_tool_warns_by_default",
    ),
}

# Documented, ASSERTED exemptions — published in the README (the conformance kit). A framework
# that cannot satisfy a scenario records WHY here rather than skipping silently.
# Every adapter but CrewAI sends through our shared httpx client (#740), so
# CrewAI is the only one listed for a transport scenario, ADK's model()
# included since its refusals are bridged too (#969). CrewAI's structural
# reason: the native OpenAI provider builds both its sync OpenAI and its AsyncOpenAI from
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
# the transport raises its own errors for a call the SDK never saw, so the bridge
# leaves them as they are rather than guess at their shape. Mirrors
# typed_refusals=False in the factory's AdapterCapabilities (#726). ADK's model()
# is not listed: LiteLLM re-raises a refusal around a response it rebuilt, and
# the adapter's llm_client tags that error with the refusal our transport
# recorded for the call (#969).
_CREWAI_REFUSAL_EXEMPTION = (
    "CrewAI's native OpenAI provider owns the transport, so its errors come from a "
    "client the SDK never saw; the typed-refusal bridge passes them through rather "
    "than classify a response our transport did not send (#724)."
)

# refusal_handler_cannot_unrefuse: donkey.on(...) reactions run at our
# transport's _on_refusal hook (#208), which a framework that owns the transport
# never reaches. Mirrors transport="framework" in its AdapterCapabilities (#726).
_CREWAI_HANDLER_EXEMPTION = (
    "CrewAI's native OpenAI provider owns the transport, so its refusals never "
    "reach our _on_refusal hook and donkey.on(...) handlers do not run for it (#208)."
)

# custom_policy_typed: a registered shape is typed by the same bridge and
# handled by the same _on_refusal hook as a built-in one (#210), so it is exempt
# for the same structural reason as the two scenarios above.
_CREWAI_CUSTOM_POLICY_EXEMPTION = (
    "CrewAI's native OpenAI provider owns the transport, so a refusal shape "
    "registered with classify.register() is neither bridged to its type nor seen "
    "by donkey.on(...) handlers for its calls (#210)."
)

# agent_killed_stops_cleanly: the kill latches into the Budget and flushes spans
# at our transport's _governed_send (#220), which CrewAI's calls never pass
# through. Mirrors transport="framework" in its AdapterCapabilities (#726).
_CREWAI_KILL_EXEMPTION = (
    "CrewAI's native OpenAI provider owns the transport, so a kill-switch refusal "
    "never reaches our transport: it does not stop donkey.budget.pace() loops or "
    "flush spans, and CrewAI's own rate-limit retry decides whether it is re-sent "
    "(#220)."
)

# approval_round_trip: the pause and the approver's span event are client-side
# and framework-free, but the resumed tool's model call is judged through our
# transport (#211, #212), which CrewAI's calls never pass through.
_CREWAI_APPROVAL_EXEMPTION = (
    "CrewAI's native OpenAI provider owns the transport, so a resumed tool's model "
    "call carries neither the approval's correlation id nor a typed refusal; the "
    "pause and the approver's span event work as for any Python code (#211)."
)

# multi_tool_multi_server: the five whole-server bindings hand the framework one
# native object per MCP server (ADK McpToolset, Strands MCPClient, OpenAI Agents
# MCPServerStreamableHttp, Agent Framework MCPStreamableHTTPTool, CrewAI
# MCPServerAdapter), which takes an allow-list of tool names but has no way to
# rename one tool. A clash across servers is refused before anything binds, and
# filter(deny=[...]) keeps one of each (tools/session.py _plan, #221, #991). Not
# a transport limit, so it mirrors no AdapterCapabilities flag.
_WHOLE_SERVER_COLLISION = (
    "{binding} binds each MCP server as one {cls}, which takes an allow-list of "
    "tool names but cannot rename a single tool, so two servers exposing the same "
    "name are refused with ConfigError before anything binds; filter(deny=[...]) "
    "keeps one of each (#221, #991)."
)

# tool_access_denied_typed: a whole-server binding hands the framework the MCP
# server, so the framework makes the tools/call and the SDK never sees the
# answer. What each does with the gateway's denial was recorded with
# scripts/mcp_binding/probe_denial.py (docs/verified-apis.md §9, #312): OpenAI
# Agents, Agent Framework and CrewAI raise (the mcp error itself, or wrapped);
# ADK and Strands turn it into an error result the model reads. Not a transport
# limit, so it mirrors no AdapterCapabilities flag.
_WHOLE_SERVER_DENIAL_RAISED = (
    "{binding} lets {cls} make the tool call, and it raises the gateway's tool-access "
    "denial as the mcp package's error{wrapped}, not ToolNotInProfile; "
    "donkey_kit.experimental.tool_access_denial(exc) converts it (#312)."
)
_WHOLE_SERVER_DENIAL_RESULT = (
    "{binding} lets {cls} make the tool call, and it turns the gateway's tool-access "
    "denial into an error result the model reads ('...Unable to access'), so nothing is "
    "raised to catch; the denial is the ToolSet's to prevent, by not binding the tool (#312)."
)

KNOWN_LIMITATIONS: dict[str, dict[str, str]] = {
    # CrewAI raises a framework error the bridge cannot type. ADK records no
    # exemption: adk.gemini() (#955) and adk.model() (#969) are both bridge-tested
    # in test_typed_refusal_bridge.py.
    "crewai": {
        "correlation_id_propagated": _CREWAI_TRANSPORT_EXEMPTION,
        "gateway_identity_observed": _CREWAI_LAST_CALL_EXEMPTION,
        "jwt_token_refreshed": _CREWAI_JWT_EXEMPTION,
        "typed_refusal_bridged": _CREWAI_REFUSAL_EXEMPTION,
        "refusal_handler_cannot_unrefuse": _CREWAI_HANDLER_EXEMPTION,
        "custom_policy_typed": _CREWAI_CUSTOM_POLICY_EXEMPTION,
        "agent_killed_stops_cleanly": _CREWAI_KILL_EXEMPTION,
        "approval_round_trip": _CREWAI_APPROVAL_EXEMPTION,
        "multi_tool_multi_server": _WHOLE_SERVER_COLLISION.format(
            binding="ToolSet.crewai()", cls="crewai_tools MCPServerAdapter"
        ),
        "tool_access_denied_typed": _WHOLE_SERVER_DENIAL_RAISED.format(
            binding="ToolSet.crewai()", cls="crewai_tools MCPServerAdapter", wrapped=""
        ),
    },
    "adk": {
        "multi_tool_multi_server": _WHOLE_SERVER_COLLISION.format(
            binding="ToolSet.adk()", cls="McpToolset"
        ),
        "tool_access_denied_typed": _WHOLE_SERVER_DENIAL_RESULT.format(
            binding="ToolSet.adk()", cls="McpToolset"
        ),
    },
    "strands": {
        "multi_tool_multi_server": _WHOLE_SERVER_COLLISION.format(
            binding="ToolSet.strands()", cls="MCPClient"
        ),
        "tool_access_denied_typed": _WHOLE_SERVER_DENIAL_RESULT.format(
            binding="ToolSet.strands()", cls="MCPClient"
        ),
    },
    "agent_framework": {
        "multi_tool_multi_server": _WHOLE_SERVER_COLLISION.format(
            binding="ToolSet.agent_framework()", cls="MCPStreamableHTTPTool"
        ),
        "tool_access_denied_typed": _WHOLE_SERVER_DENIAL_RAISED.format(
            binding="ToolSet.agent_framework()",
            cls="MCPStreamableHTTPTool",
            wrapped=" (wrapped in its ToolExecutionException)",
        ),
    },
    # The OpenAI Agents SDK's model calls record no exemption: deep since #223,
    # every transport scenario runs against a Runner.run surface,
    # approval_round_trip since #213. Its ToolSet binding is a whole-server one.
    "openai_agents": {
        "multi_tool_multi_server": _WHOLE_SERVER_COLLISION.format(
            binding="ToolSet.openai_agents()", cls="MCPServerStreamableHttp"
        ),
        "tool_access_denied_typed": _WHOLE_SERVER_DENIAL_RAISED.format(
            binding="ToolSet.openai_agents()", cls="MCPServerStreamableHttp", wrapped=""
        ),
    },
}

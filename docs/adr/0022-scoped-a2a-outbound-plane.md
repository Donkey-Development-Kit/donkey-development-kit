# ADR 0022: Outbound A2A uses a scoped credential plane and a provisional facade over the official SDK

- **Status:** Proposed (the scoped implementation and provisional API tier
  enforcement are present in #1395; user acceptance of the decision is pending)
- **Date:** 2026-11-03
- **Issue:** #1395 (BG §2.9). Extends ADR 0014's A2A package and ADR 0009's
  shared transport; amends ADR 0006's root-export tier for this new facade.

## Context

`a2a/client.py` implements outbound sends and streams using the official
`a2a-sdk`. The LLM client already owns the governed transport, budget and
response-observation hooks, but its credentials, model retries and GenAI SSE
scanner are wrong for a delegated agent action. A card can advertise an
upstream URL that must never replace the registry's gateway endpoint.
`AgentHandle` is an immutable resolution result, not an invocation client.

## Decision

1. **Protocol handling stays in `a2a`, beside `integrations`.** The SDK's
   `JsonRpcTransport` and v0.3 `CompatJsonRpcTransport` handle JSON-RPC v1 and
   v0.3 respectively. REST and gRPC are outside this surface. Core has no A2A
   SDK or framework dependency; it implements only HTTP scoping and run context.
2. **The outbound plane is a distinct, non-owning view of the shared async
   transport.** `DonkeyAsyncClient.a2a_view()` permits only the exact resolved
   endpoint and GET of its pinned `.well-known/agent-card.json`. It never adds
   that origin to the LLM client's allowlist. Redirects, card-advertised
   retargeting and request-hook destination changes fail closed. Shared LLM
   default headers, wallet selectors, provider keys, attribution, cache and
   cost headers do not leak into this plane. `test_a2a_transport.py` pins it.
3. **Governance precedes gateway traffic.** Each operation takes an Exchange
   `AssetRef | str`, calls `explain()` with `GovernanceCriteria()` by default
   or the explicit `criteria=`, refuses a failing verdict, then resolves the
   gateway endpoint. It never accepts an `AgentHandle` or an arbitrary URL.
   This is endpoint pinning for that operation, not the planned lockfile.
4. **Authentication is acquired per HTTP request.** The callback checks
   derived-endpoint provenance before obtaining `McpAuth.headers()`, using
   explicit `auth=` or configured consumer auth. It reads the current
   `as_user()` scope each time and rejects a service/user header collision.
   No 401 invalidation/re-send, 5xx retry, refusal-action replay, stream
   resubscription or OAuth challenge completion is performed.
5. **The caller owns the stream context.** `send(ref, message, *, auth=None,
   criteria=None)` is async and returns SDK `SendMessageResponse` for SDK
   `SendMessageRequest`. `async with stream(...) as events` yields an async
   iterator of SDK `StreamResponse`. Context exit closes local responses,
   SDK transport, generators and span, including break, error and cancellation;
   it does not send a remote task-cancel method or close the shared pool.
6. **One outbound CLIENT span covers card discovery and delegation.**
   `core.inbound.outbound_call()` opens `donkey.a2a.call`, preserves the
   enclosing correlation ID/source or binds a fresh one, and propagates W3C
   trace context without baggage. Team/project/environment may be span tags,
   never end-user IDs or content. Failures record exception type only. A2A
   responses are not scanned as GenAI token streams. The transport still runs
   its budget, `last_call` and refusal-observation seams.
7. **`A2AFacade` and `Donkey.a2a` are provisional API.** This new root export
   is an explicit exception to ADR 0006's stable-root rule: its send/stream
   signatures may change in a minor release with a migration note. The
   accessor must carry ADR 0020's docstring mark and live in
   `PROVISIONAL_MEMBERS`; the root export still belongs in `ROOT_ALL` so its
   addition is reviewed. Generated API docs must preserve this exception,
   rather than describing all root exports as stable. The docstring marks,
   hard-coded snapshot and generated stability prose enforce this tier in
   #1395; this ADR remains Proposed pending user acceptance of the decision.
8. **Evidence is OFFLINE-VERIFIED, not a gateway success claim.** Installed
   SDK symbols and mock HTTP send/SSE tests back §8.5 of the verification
   ledger. Successful gateway send and SSE are NOT live verified by this
   implementation yet. Captured refusals before SSE are not streaming success
   evidence. `AgentHandle.as_tool()` and native framework bindings stay planned.

### Alternatives considered

- **Reuse the ordinary LLM view unchanged.** Rejected: it would inherit model
  credentials, replay behavior and GenAI parsing for an agent action.
- **Build an independent HTTP pool or protocol implementation.** Rejected:
  it bypasses the skeleton or duplicates the official A2A SDK.
- **Follow card URLs or mutate the resolved handle.** Rejected: it can route
  credentials around the governed endpoint; resolution stays immutable.
- **Automatically cancel the remote task when iteration stops.** Rejected:
  local cleanup is not authorization for another remote action.

## Consequences

The SDK's own telemetry decorators ignore generic OTel suppression and can export
raw SDK parser diagnostics independently of DDK's `telemetry` setting. The
approved boundary defaults `allow_a2a_sdk_telemetry` to false: before registry
reads or HTTP, calls require known-disabled imported SDK instrumentation. Hosts
can disable it before import with `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`,
or explicitly accept its diagnostic-recording risk with
`allow_a2a_sdk_telemetry=True` / `DONKEY_ALLOW_A2A_SDK_TELEMETRY=true`.
Changing the opt-out after import does not disable instrumentation. Unknown SDK
state fails closed. DDK's own tracing remains independent and no process-global
instrumentation or environment is changed. The ineffective unsampled-parent
mitigation is removed; explicit opt-in preserves actual SDK span parentage.

`test_a2a_facade.py`, `test_a2a_transport.py`, `test_a2a_outbound_spans.py` and
`tests/conformance/test_a2a_outbound.py` cover lazy imports, request scope,
auth isolation, official SDK v1/v0.3 objects, refusal safety, correlation and
stream lifecycle locally. The public snapshot is hard-coded in
`tests/unit/test_public_api_snapshot.py`, not a fixture; the parent change must
review its generated delta and the provisional tier. Acceptance and a real
gateway send/SSE smoke test remain separate follow-up work.

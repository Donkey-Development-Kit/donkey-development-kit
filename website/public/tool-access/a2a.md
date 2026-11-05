# Outbound A2A

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

`donkey.a2a.send()` and `donkey.a2a.stream()` delegate to a governed agent using
the official `a2a-sdk`. The `A2AFacade` API is **provisional**: it may change in
a minor release with a migration note. Framework-native `AgentHandle.as_tool()`
binding remains planned.

  **OFFLINE-VERIFIED:** official SDK v1/v0.3 send and SSE behavior is tested
  locally with mock HTTP. Successful gateway send and SSE are **NOT live
  verified by this implementation yet**. Captured refusals, including refusals
  to streaming methods before SSE starts, are not streaming success evidence.

## Install

```bash
pip install "donkey-kit[a2a]"
```

The same extra backs [`donkey.serve()`](https://docs.donkey-kit.dev/a2a.md). Accessing `donkey.a2a` does not
with the install command before registry requests.

## Send and stream

Pass an Exchange `group_id/asset_id/version` reference and an official SDK
`SendMessageRequest`. The async send returns an SDK `SendMessageResponse`,
not a DDK task wrapper or a framework tool:

```python
from a2a.types import Message, Part, Role, SendMessageRequest
from donkey_kit import Donkey

request = SendMessageRequest(
    message=Message(
        message_id="triage-1",
        role=Role.ROLE_USER,
        parts=[Part(text="Summarize this claim.")],
    )
)
ref = "com.acme/claims-triage-agent/1.0.0"

async with Donkey.from_env() as donkey:
    response = await donkey.a2a.send(ref, request)
    print(response.WhichOneof("payload"))

    async with donkey.a2a.stream(ref, request) as events:
        async for event in events:  # each event is a2a.types.StreamResponse
            print(event.WhichOneof("payload"))
```

Inspect the returned message or task, including its status: an HTTP success
does not mean the remote task completed. Use a distinct message ID for each
new action in your application; the example shows the same request shape for
the two alternative calling forms.

Both methods have keyword-only `auth=None` and `criteria=None`. The default
criteria are `GovernanceCriteria()`: require an API instance, deployment and
at least one policy, with unknown checks failing. Pass explicit criteria when
your organization requires named policies, gateways, tags or governance checks:

```python
from donkey_kit import GovernanceCriteria

criteria = GovernanceCriteria(required_policies=["client-id-enforcement"])
response = await donkey.a2a.send(ref, request, criteria=criteria)
```

Each operation calls `donkey.registry.explain()` before resolving the gateway
endpoint. A failed governance verdict raises `RegistryError` before any card
or send request reaches the gateway. This is a registry check, not local policy
enforcement; gateway policies still decide the actual request.

## Authentication and lifecycle

The official SDK's tracing is independent of DDK tracing and can export private
error diagnostics. By default, disable it before importing `a2a` with
`OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`. If you deliberately want both
recorders, use `Donkey.from_env(allow_a2a_sdk_telemetry=True)` to accept that risk.
Neither `telemetry=True` nor `telemetry=False` supplies this permission. Calls
refuse enabled or unknown SDK tracing before registry access unless you opt in.
See [Outbound A2A spans](https://docs.donkey-kit.dev/telemetry.md#outbound-a2a-spans).

`auth=` accepts the same `McpAuth` credential protocol as MCP bindings, including
`ClientIdAuth` and `BearerAuth` from `donkey_kit.tools.auth`. Without an
override, configured consumer auth is used: the client-ID pair, or the
`llm_auth` bearer provider in JWT/bearer mode. The connected-app credential
belongs only to the registry's control-plane reads.

Credentials and the current `donkey.as_user()` token are read for **each HTTP
request**, including the card GET. Derived-endpoint provenance is checked
before acquiring credentials. Service and user credentials must use distinct
header names; a collision raises `ConfigError`. LLM wallet selectors, provider
keys, attribution, cache-control and cost headers are not inherited.

No automatic retries, 401 refresh/re-send, refusal-handler replay, stream
resubscription or OAuth completion occur. This is true even when model retry
options are enabled. Decide whether a new delegated action is safe yourself.

Always consume streams inside `async with`. Leaving the context, including a
break, error or task cancellation, closes local responses and generators and
ends the outbound span. It does **not** cancel the remote task. The shared
HTTP pool remains usable for later calls.

## Where this fits

- `AgentHandle` remains an immutable, resolution-only value from
  `ExchangeRegistry.resolve_agent()`. Pass the Exchange reference to the facade,
  not a handle, mutated handle or arbitrary URL. See
  [Resolving an A2A agent](https://docs.donkey-kit.dev/tool-access/discovery.md#resolving-an-a2a-agent).
- The resolved URL follows the
  [`https://` rule](https://docs.donkey-kit.dev/reference/configuration.md#endpoints-must-use-https) of the
  URLs you configure, because the gateway credential is sent to it. A gateway
  public URL that uses plain `http://` to a non-loopback host (without
  `DONKEY_ALLOW_HTTP=1`), or that carries a user name or password, raises
  `RegistryError`, and nothing is sent to the agent.
- The registry endpoint is pinned for each operation. Card-advertised URLs
  never retarget the call, and redirects are not followed. Only that exact
  endpoint and GET of its agent-card URL can use the scoped transport.
- Only JSON-RPC v1 and v0.3 are in scope. The official SDK selects the v0.3
  compatibility transport for a legacy card; REST and gRPC are rejected.
  Each send or stream POST carries `A2A-Version` from the selected card
  interface. Explicit interfaces without a supported version are rejected;
  older cards without `supportedInterfaces` use the official SDK's legacy
  version default when `protocolVersion` is omitted.
- Framework-native `as_tool()` binding remains planned, following
  [Framework binding](https://docs.donkey-kit.dev/tool-access/binding.md). No binding method is added to the handle.
- To make *your* agent callable by others over A2A, see [A2A agents](https://docs.donkey-kit.dev/a2a.md).

## Gateway Refusals

The [A2A gateway policy inventory](https://docs.donkey-kit.dev/a2a-gateway-policies.md) separates v0.3/v1
captures, typed outcomes, non-blocking annotations and unverified behavior.

Gateway policies can reject a remote A2A call before it reaches the agent.
For A2A v0.3, PII detection returns HTTP 403 with a JSON-RPC error whose
`data` string can contain the detected value. Schema validation returns HTTP
400 with a JSON-RPC error, and token rate limiting can return HTTP 429 with
an empty body and `x-token-*` headers. Do not log raw PII refusal bodies.

For A2A v1, PII detection returns HTTP 403 with a JSON-RPC `ErrorInfo`
detail (`FORBIDDEN`, domain `a2a-protocol.org`). Both the error message and
its detail can contain the detected value. Schema validation uses the same
detail envelope: bad parameters and malformed JSON return HTTP 400, while
an unknown method returns HTTP 404. Token rate limiting can return a bare
HTTP 429 with the same `x-token-*` headers. A rejected streaming request can
return an ordinary HTTP error before any SSE stream starts.

Azure Content Guardrails can return HTTP 403 with `FORBIDDEN` and a
`prompt_shield` detail. Fail-closed Azure and Bedrock guardrail service failures
return HTTP 503 with `UNAVAILABLE`. The `x-flex-azure-guardrails-*` or
`x-flex-bedrock-guardrails-*` headers identify the action, phase and reason.
A `service_unavailable` refusal is not a content-moderation verdict. These
captures cover request-phase refusals, not response moderation or successful SSE.

You can explicitly classify the captured Azure HTTP 403 reject headers as
`ContentSafetyBlocked` using `classify(response)` or `jsonrpc_refusal(...)`
with HTTP status and headers. The captured bare token-rate-limit HTTP 429 is
`TokenBudgetExceeded`; its reset is exposed in seconds. HTTP 503 remains
`UpstreamModelError`, not a content block or a no-response `GatewayUnavailable`.
PII and schema failures remain ambiguous: generic protocol codes and `ErrorInfo`
reasons do not identify those policies. Their diagnostics are not used for
typing or repeated in exception text. See [JSON-RPC refusal matching](https://docs.donkey-kit.dev/errors.md#json-rpc-refusal-matching).

## Authentication Challenges

The v0.3 In-Task Authorization Code policy can return HTTP 200 with a
JSON-RPC **result**, not an error: a task whose status is `auth-required`.
Its status message contains an `authChallenge` data part with OAuth metadata,
including authorization/token endpoints, scopes, audience and redirect URI.
A `message/stream` challenge can also be ordinary JSON rather than SSE.
HTTP success alone therefore does not mean the delegated task completed.

For this complete v0.3 challenge, both outbound facade methods raise
`GatewayAuthRequired` rather than return an apparently successful task.
Catch it around `send()` or stream iteration:

```python
from donkey_kit import GatewayAuthRequired

try:
    response = await donkey.a2a.send(ref, request)
except GatewayAuthRequired as pending:
    challenge = pending.challenge
    # Hand challenge to your application's OAuth flow after validating its URLs.
```

`challenge` is a frozen `GatewayAuthChallenge` with `secondary_auth_provider`,
`authorization_endpoint`, `token_endpoint`, `scopes` (a tuple), `audience`,
`redirect_uri`, `response_type`, `code_challenge_method` and `body_encoding`.
The exception also keeps the **returned** `task_id` and `context_id`, the raw
HTTP `response`, and the usual correlation/call/request IDs. Challenge values
are omitted from repr and error text, but explicit field access and the raw
response can expose sensitive data. Treat the metadata as untrusted.

This is not `GatewayApprovalPending`: `wait_for_approval()` does not poll it.
It is neither a refusal nor a local `ApprovalRequired`. A normal HTTP 401 still
raises `AuthError` with no automatic replay. DDK does not acquire a token,
follow the challenge URLs, host a redirect, or resend the task. Successful token
resubmission is not verified. Incomplete or unknown challenge layouts and v1
tasks remain native SDK responses; there is no captured v1 challenge contract.

The captured missing-token responses generated new task and context IDs,
despite IDs supplied in the request params, and returned the numeric JSON-RPC
request ID as a string. These observations do not verify token forwarding,
caching, authentication failures or a completed OAuth flow.

## Quality Evaluation

Quality Evaluation can annotate a successful agent response with
`x-quality-evaluation`, `x-quality-score`, `x-quality-explanation` and
`x-quality-status` headers. A captured v0.3 `message/send` reply returned
HTTP 200 with `x-quality-evaluation: yes`, `x-quality-score: 3` and
`x-quality-status: completed`; its explanation described how the agent fulfilled
the request. That single response does not establish a score range, evaluator
failure behavior, v1 evaluation or streaming support. A separate bounded v0.3
send configured with a deliberately invalid synthetic evaluator key returned
HTTP 200 JSON with an agent `PONG` message and only `x-quality-status: pending`,
without score, evaluation or explanation headers. This observation does not
establish an internal evaluator cause or mean that `pending` is failure; those
failure semantics, v1 evaluation and streaming remain unverified. The selected
headers are test-only evidence, not a runtime or replay change or live SDK
facade success. On `develop` (not
published `0.2.0`), DDK records quality annotations from successful outbound
POST responses on its [A2A spans](https://docs.donkey-kit.dev/telemetry.md#outbound-a2a-spans). This recording
is tested offline, including mock streaming; it is not live facade or streaming
verification. Explanation text requires `telemetry_capture_content=True`, is
not sanitized and is limited to 4096 characters. Agent Card GETs and failed
HTTP responses do not contribute quality annotations.

The outbound facade classifies non-success HTTP responses and buffered JSON-RPC
errors before the SDK can render sensitive diagnostics. Protocol/parse failures
become a safe `ToolInvocationError`. Successful streams stay unread by the
core transport and are decoded by the official SDK, not the GenAI SSE scanner.
Raw responses on exceptions remain potentially sensitive. This does not add
A2A PII/schema-specific typing or framework refusal translation.

`as_tool()` remains planned. [`donkey dev`](https://docs.donkey-kit.dev/a2a.md#simulated-ingress) defaults to
v1 ingress replay; `--protocol-version 0.3.0` selects legacy PII/rate
`message/send` captures, not this outbound authorization-code challenge.
Its local unsupported-call HTTP `400` is not a gateway policy refusal.
With scenarios configured, legacy streaming fails locally; without them it
passes through to the agent. See the [replay matrix](https://docs.donkey-kit.dev/simulator.md#a2a-ingress-replay).
The CLI flag selects the local listener/ingress, not this outbound facade,
which still selects the official compatibility transport from the remote card.

## Next steps

- [Telemetry and correlation](https://docs.donkey-kit.dev/telemetry.md#outbound-a2a-spans)
- [Typed errors and retry behavior](https://docs.donkey-kit.dev/errors.md)
- [Serve your own A2A agent](https://docs.donkey-kit.dev/a2a.md)

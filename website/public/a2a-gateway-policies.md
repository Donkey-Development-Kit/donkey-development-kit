# A2A gateway policies

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

Omni Gateway policies run on the remote agent's API instance. DDK does not
evaluate them locally: it resolves the governed endpoint, delegates through the
official `a2a-sdk`, and surfaces outcomes it can identify safely. Use
[`donkey.a2a.send()` or `donkey.a2a.stream()`](https://docs.donkey-kit.dev/tool-access/a2a.md) for outbound
delegation, and [`donkey.serve()`](https://docs.donkey-kit.dev/a2a.md) to make your own agent callable.

  **Live captures are not live SDK verification.** The response shapes below
  include the 2026-11-02 Omni Gateway 1.14.4 captures and later bounded send-header captures. DDK classification,
  outbound delegation and selected ingress replay are **OFFLINE-VERIFIED**.
  Successful gateway send and SSE through the facade are not live verified. One v1
  facade send was captured; the facade's v0.3 transport is an **accepted boundary**,
  because the live gateway card advertises only a v1 interface, so the facade selects
  the v1 transport there.
  A refusal to a streaming method before SSE starts does not prove streaming success.

The outbound facade and auth-challenge types are available on `develop`, not
yet in published `0.2.0`. `A2AFacade` is provisional; framework-native
`AgentHandle.as_tool()` binding remains planned.

## Protocol versions

| Contract | A2A v0.3 | A2A v1 |
|---|---|---|
| Send methods | `message/send`, `message/stream` | `SendMessage`, `SendStreamingMessage` |
| Capture request version header | No `A2A-Version` | `A2A-Version: 1.0` |
| PII/schema diagnostics | JSON-RPC `error.data` string | `google.rpc.ErrorInfo`, domain `a2a-protocol.org` |
| Unknown-method schema refusal | HTTP `400`, code `-32601` | HTTP `404`, code `-32601`, reason `METHOD_NOT_FOUND` |

The official SDK selects the v0.3 compatibility transport for a legacy card.
This outbound surface supports JSON-RPC, not REST or gRPC. A v1 upstream alone
does not make an API Manager registration compatible: the capture setup required
a separate `a2a_v1` instance; attaching the v1 PII policy to the existing `a2a`
instance was rejected. This does not verify SDK v1 publication, `expose` or
dual-card publication.

## Captured outcomes

Each **VERIFIED (LIVE)** label applies only to the stated response shape and
policy version, not every configuration or SDK integration. The DDK column is
offline-tested handling by the facade or explicit `classify(response)`.
`ToolInvocationError` is imported from `donkey_kit.experimental`; the other
exception types below are root exports.

| Policy and version | Evidence | Captured response | DDK outcome |
|---|---|---|---|
| v0.3 PII Detector, `a-two-a-pii-detector` 1.0.2 | VERIFIED (LIVE) | `message/send`: `403`, code `-32602`, string findings in `error.data` | Safe `ToolInvocationError`, **not** `PIIDetected` |
| v1 PII Detector, `a2a-v1-pii-detector` 1.0.0 | VERIFIED (LIVE) | Both send methods: `403`, code `-32600`, reason `FORBIDDEN` | Safe `ToolInvocationError`, **not** `PIIDetected` |
| v0.3 Schema Validation, `a-two-a-schema-validation` 1.0.2 | VERIFIED (LIVE) | Bad parameters `400` / `-32602`; unknown method `400` / `-32601`; malformed JSON `400` / `-32700` | Safe `ToolInvocationError`, no schema-policy-specific type |
| v1 Schema Validation, `a-two-a-v1-schema-validation` 1.0.1 | VERIFIED (LIVE) | Bad parameters `400` / `INVALID_PARAMS`; unknown method `404` / `METHOD_NOT_FOUND`; malformed JSON `400` / `PARSE_ERROR` | Safe `ToolInvocationError`, no schema-policy-specific type |
| v0.3/v1 Token Based Rate Limit, `a-two-a-token-rate-limit` / `a-two-a-v1-token-rate-limit` 1.0.1 | VERIFIED (LIVE) | Oversized send request; v1 also streaming: empty `429`, `x-token-limit: 1`, `x-token-remaining: 1`, `x-token-reset: 60000`, no `retry-after` | `TokenBudgetExceeded`, `.retry_after` is 60 seconds |
| v1 Azure Content Guardrails, `a2a-v1-azure-guardrails` 0.1.0 | VERIFIED (LIVE) | Both methods: request-phase Prompt Shield `403`, `x-flex-azure-guardrails-action: reject`, reason `prompt_shield` | `ContentSafetyBlocked(provider="azure-content-safety")`, header-derived categories |
| Same Azure policy, fail-closed service failure | VERIFIED (LIVE) | Both methods: `503`, `UNAVAILABLE`, reason `service_unavailable`, induced with an invalid key | `UpstreamModelError`, **not** a content block |
| v1 Bedrock Guardrails, `a2a-v1-bedrock-guardrails` 1.0.0 | VERIFIED (LIVE) | Both methods: fail-closed `503`, `UNAVAILABLE`, `x-flex-bedrock-guardrails-*`, nonexistent guardrail version | `UpstreamModelError`; no Bedrock content-block capture |
| v0.3 In-Task Authorization Code, `intask-authorization-code-policy` 1.0.1 | VERIFIED (LIVE) | Both methods: `200` JSON task, `auth-required`, complete status-message `authChallenge`; no SSE | `GatewayAuthRequired` after SDK decoding, **not** a refusal or completed task |
| v0.3 Quality Evaluation, `a2a-quality-evaluation-policy` 1.0.0 | VERIFIED (LIVE) | `message/send`: one `200` completed-evaluation capture; a separate invalid-evaluator-key configuration returned `200` with only `x-quality-status: pending` | Completed-evaluation span annotation is offline-tested; pending capture adds no runtime or replay behavior |

PII/schema envelopes lack reliable policy identifiers. Generic codes,
`FORBIDDEN` / `INVALID_PARAMS` reasons and diagnostic text are not policy
discriminators; `jsonrpc_refusal(...)` returns `None` for these ambiguous errors.
For the Azure block it requires HTTP status and headers. Use
`classify(response)` for the bare token-policy `429`. See
[JSON-RPC refusal matching](https://docs.donkey-kit.dev/errors.md#json-rpc-refusal-matching).

Positive `x-token-remaining` does not mean a request fits the window. Reset
values are milliseconds on the wire and seconds on the exception; accepted-call
v0.3 send rate headers are also captured on HTTP 200 (`x-token-limit: 1000`,
`x-token-remaining: 995`, `x-token-reset: 55454`). A separate accepted v1
`SendMessage` capture returned HTTP 200 with limit `1000`, remaining `995` and
reset `56893` ms. These are individual observed windows, not deterministic token
accounting, successful streaming or SDK budget updates on A2A calls.
A fail-closed `503` means the service could not
complete its check, not that it judged content unsafe. `GatewayUnavailable`
instead requires **no HTTP response**.

## Challenges and replay

The complete captured v0.3 challenge raises `GatewayAuthRequired` from either
facade method. Its frozen `GatewayAuthChallenge` holds nine required fields:
`secondary_auth_provider`, `authorization_endpoint`, `token_endpoint`, `scopes`
(a tuple), `audience`, `redirect_uri`, `response_type`, `code_challenge_method`
and `body_encoding`. The exception preserves the **returned** task/context IDs
and raw response. The captures generated new IDs despite supplied request IDs;
do not substitute the IDs you sent.

Validate challenge metadata before using it. DDK does not follow its URLs,
acquire tokens, host a redirect, poll CIBA or resend the task. This is distinct
from `AuthError`, `GatewayApprovalPending` and local `ApprovalRequired`.
Unknown/incomplete layouts and all v1 tasks stay native SDK responses; the
documentation limits the In-Task Authorization Code policy to v0.3.0, so DDK does
not convert a v1 challenge. OAuth completion, token forwarding/removal, caching
and authorization-policy `401` responses remain **UNVERIFIED**: a bounded probe with a synthetic invalid token and unvalidatable OAuth metadata got no policy-generated `401` (the request passed through to the agent), so DDK treats that `401` as an accepted boundary. See
[Authentication challenges](https://docs.donkey-kit.dev/tool-access/a2a.md#authentication-challenges).

The outbound plane never automatically replays a delegated action, even for
`401`, `429` or `503`; model retry settings do not change that. Leaving an
`async with` stream context closes local resources, not the remote task.
Decide whether a new action is safe in your application.

## Non-blocking policies

Quality Evaluation's captured headers are `x-quality-evaluation: yes`,
`x-quality-score: 3`, `x-quality-status: completed` and `x-quality-explanation`.
They describe one response, not a score range or deterministic grading guarantee.
A separate bounded v0.3 send on 2026-11-05, configured with a deliberately invalid
synthetic evaluator key, returned HTTP 200 JSON with an agent `PONG` message and
only `x-quality-status: pending`. No score, evaluation or explanation header was
observed. `pending` does not prove evaluator failure or an internal cause.
Evaluator failure semantics and streaming evaluation remain **UNVERIFIED**.
v1 evaluation is an **accepted boundary**: the Omni Gateway 1.14 documentation
limits Quality Evaluation to A2A v0.3.0, and a v1 `SendMessage` through the policy
returned HTTP 200 with no `x-quality-*` headers, so DDK does not expect them on v1.
This header-only observation adds no runtime or simulator replay
behavior and does not verify live SDK facade success or SSE.
DDK records known evaluation/status values and finite numeric scores on
`donkey.a2a.call` spans from successful delegation HTTP responses. Explanations
require `telemetry_capture_content=True` and are limited to 4096 characters;
they can repeat sensitive request content. This recording is **OFFLINE-VERIFIED**,
available on `develop` but not published `0.2.0`. See the
[quality span contract](https://docs.donkey-kit.dev/telemetry.md#outbound-a2a-spans).

Agent Card URL rewriting is **VERIFIED (LIVE)** for one JSONRPC card GET with
definition `a-two-a-v1-agent-card` 1.0.1 and implementation 1.0.2 on gateway
1.14.4. An explicit `consumerUrl` rewrote both advertised interface URLs to the
public gateway base, preserving versions `1.0` and `0.3.0` and all non-URL
interface fields. With that setting omitted, the observed URLs used `localhost`,
not the public host: check the returned card before giving its URLs to callers.
Other card fields changed; this does not verify whole-card preservation, other
bindings, extended-card responses, SSE or gateway call metrics.

The v1-to-v0.3 Transcoder (`a-two-a-v1-to-v03-transcoder` 1.0.0) is **VERIFIED (LIVE)**
only for its unsupported-method refusals: `ListTasks` returned HTTP `400` / `-32004`
and a push-notification method `400` / `-32003`, both `google.rpc.ErrorInfo`. The
documented `-32601` for `ListTasks` was not observed. Successful transcoding is not
captured. Prompt Decorator remains uncaptured. Do not infer a typed refusal or
successful transcoding from an applied-policy list. Connection telemetry using `X-AGENT-ID` is also
not established by these captures, so DDK does not inject that header on MCP
or A2A calls. The existing
[outbound span contract](https://docs.donkey-kit.dev/telemetry.md#outbound-a2a-spans) is separate from gateway
annotations and the official SDK's own instrumentation.

## Offline testing and privacy

[`donkey dev`](https://docs.donkey-kit.dev/a2a.md#simulated-ingress) defaults to `--protocol-version 1.0`,
replaying selected v1 ingress Azure Prompt Shield, PII and token-rate refusals
for both send methods. `--protocol-version 0.3.0` uses the existing legacy PII
and token-rate captures for `message/send` only. See the
[scenario/version/method matrix](https://docs.donkey-kit.dev/simulator.md#a2a-ingress-replay).

Legacy `injection` is rejected at startup. Legacy `message/stream` with any
configured scenarios returns local plain-text HTTP `400` before advancing a
rule, even if no rule would reject; without scenarios it passes through.
Known wrong-version call methods and explicit mismatched version headers on
calls also return local `400`, not a captured or fabricated gateway refusal.
Unknown methods still reach the agent's official SDK routes, so these simulator
checks do not reproduce the captured schema-policy unknown-method errors above.

It does not enforce policies, rewrite captured request IDs, simulate OAuth or
provide outbound A2A `simulate()` injection. [`donkey mock`](https://docs.donkey-kit.dev/simulator.md) serves
model routes, not an A2A gateway. Real A2A policies are unsupported in Omni
Gateway Local Mode; DDK's pure-Python ingress simulator is not that mode.
Protocol selection, SDK legacy card conversion/routes and this replay matrix
are local/offline behavior only. They add no captures or live gateway evidence,
and do not verify successful gateway send/SSE, publication or exposure.

PII messages/details can echo raw findings. DDK exception text and spans withhold
those diagnostics, but `.response`, explicit challenge field access and host/SDK
instrumentation can expose them. Never log raw bodies or assume content-capture
opt-in makes auth metadata safe.

The [verification ledger](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md)
records capture scope and provenance. Bedrock content blocks and response-phase
guardrails remain **UNVERIFIED**. Successful gateway SSE is an **accepted
boundary**: the hosted agent card advertises `streaming: false`, refusals before
the stream are captured, and success is verified offline only. Offline tests do
not promote any of these claims.

## Next steps

- [Outbound A2A](https://docs.donkey-kit.dev/tool-access/a2a.md): scoped credentials and call examples.
- [Typed errors](https://docs.donkey-kit.dev/errors.md#json-rpc-refusal-matching): discriminators and diagnostics.
- [A2A ingress replay](https://docs.donkey-kit.dev/simulator.md#a2a-ingress-replay): offline scenarios.
- [Outbound A2A spans](https://docs.donkey-kit.dev/telemetry.md#outbound-a2a-spans): tracing boundaries.

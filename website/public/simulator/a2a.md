# A2A faults & replay

Status: Live. Shipped, and verified against a real governed gateway.

The [local simulator](https://docs.donkey-kit.dev/simulator.md) replays model-route refusals. This page covers
its A2A counterparts: refusing an outbound call made through `donkey.a2a` with
`donkey.simulate_a2a()`, and replaying captured A2A ingress refusals. For the
policies these captures come from, see
[A2A gateway policies](https://docs.donkey-kit.dev/a2a/gateway-policies.md).

## Outbound A2A fault injection

`donkey.simulate_a2a()` is the A2A counterpart of
[`simulate()`](https://docs.donkey-kit.dev/testing.md) and
[`simulate_mcp()`](https://docs.donkey-kit.dev/simulator/mcp.md#mcp-tool-fault-injection). It makes an agent you **call**
through [`donkey.a2a`](https://docs.donkey-kit.dev/a2a/call.md) get a captured gateway refusal, with
no gateway and no Exchange. Like `donkey.a2a`, it is provisional.

```python
import pytest
from a2a.types import Message, Part, Role, SendMessageRequest
from donkey_kit import Donkey, ToolInvocationError

request = SendMessageRequest(
    message=Message(message_id="m1", role=Role.ROLE_USER, parts=[Part(text="Email a@b.c")])
)

async def test_delegation_handles_a_pii_block():
    async with Donkey.from_env(allow_a2a_sdk_telemetry=True) as donkey:
        with donkey.simulate_a2a("pii-send", agents=["acme/triage-agent/1.0.0"]):
            with pytest.raises(ToolInvocationError):
                await donkey.a2a.send("acme/triage-agent/1.0.0", request)
            # times=1: the next call gets a simulated reply
            await donkey.a2a.send("acme/triage-agent/1.0.0", request)
```

- Inside the block, the refs in `agents` resolve without Exchange: no
  governance read and no endpoint lookup. Any other ref raises
  `RegistryError`. The agent card and the replies are served offline, through
  the official SDK's own JSON-RPC transports.
- A `-send` capture refuses `send()` only, and a `-stream` capture refuses
  `stream()` only. Every other call gets one text part,
  `"simulated reply"`, or what `reply(text)` returns.
- Every injected response carries `x-donkey-simulator: true`. Blocks do not
  nest, and after the block the refs resolve through Exchange again.
- The outbound [A2A SDK tracing gate](https://docs.donkey-kit.dev/telemetry.md#outbound-a2a-spans) applies
  offline too. With opentelemetry installed (the `[otel]` and `[all]` extras)
  and `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED` unset, the first call raises
  `ConfigError` instead of the refusal. Set the variable to `false` before
  importing `a2a` (for example in `conftest.py`), or build the client with
  `Donkey.from_env(allow_a2a_sdk_telemetry=True)`.

| `capture` | `version="1.0"` | `version="0.3"` | Raised error |
|---|---|---|---|
| `pii-send`, `pii-stream` | yes | `pii-send` only | `ToolInvocationError` |
| `azure-block-send`, `azure-block-stream` | yes | no | `ContentSafetyBlocked` |
| `rate-send`, `rate-stream` | yes | `rate-send` only | `TokenBudgetExceeded` |

The raised error is whatever the existing classification gives each packaged
Omni Gateway capture, the same bytes listed in
[A2A gateway policies](https://docs.donkey-kit.dev/a2a/gateway-policies.md). The rate-limit captures have
no body, so the error carries headers only. This covers agents you call. For
an agent you **serve**, use the ingress replay below.

## A2A ingress replay

See [A2A gateway policies](https://docs.donkey-kit.dev/a2a/gateway-policies.md) for the version-specific
capture inventory. Local replay verifies handling, not gateway enforcement.

[`donkey dev`](https://docs.donkey-kit.dev/a2a/serve.md#simulated-ingress) wraps your served agent rather than the
model routes above. It shares the fixture loader and scenario triggers, but
selects captured A2A refusals with `--protocol-version`: `1.0` by default,
or `0.3.0`. This is separate from `--agent-version`, the card's application
release version. The supported scenario/version/method matrix is:

| Protocol | Call method | `pii_block` | `request_limit` | `injection` |
|---|---|---|---|---|
| `1.0` (default) | `SendMessage` | Captured send refusal | Captured send refusal | Captured Azure send block |
| `1.0` | `SendStreamingMessage` | Captured stream refusal | Captured stream refusal | Captured Azure stream block |
| `0.3.0` | `message/send` | Captured legacy send refusal | Captured legacy send refusal | Unsupported at construction/startup |
| `0.3.0` | `message/stream` | Local HTTP `400` with any scenarios | Local HTTP `400` with any scenarios | Unsupported at construction/startup |

v0.3 streaming with configured scenarios fails before any rule advances, even
if the stream would not trigger a rejection. Without scenarios,
`message/stream` passes through with the agent's own bytes; this is not a
promise that the wrapped agent supports streaming. `build_app()` advertises
`streaming: false`.

Known call methods from the wrong version and explicit mismatched
`A2A-Version` headers on calls also return local plain-text HTTP `400`, even
without scenarios. Configuration accepts exactly `1.0` or `0.3.0`, but header
matching follows the official SDK's version normalization and compares only
the major version: `1.0.0` matches selected `1.0`, and `0.3.1` matches selected
`0.3.0`. Other parseable versions with the same major also match; malformed
versions or a different major do not. An absent header retains replay behavior;
the SDK validates passing calls. These local errors carry `x-donkey-simulator: true`, not a fabricated
gateway refusal or JSON-RPC policy error. Unknown methods, malformed JSON,
card reads and task reads/cancels pass to the agent's SDK routes.

Each supported cell uses its matching capture, with these DDK outcomes:

| Scenario | A2A ingress response | Existing classification |
|---|---|---|
| `injection` (v1 only) | Azure prompt-shield `403`, `ErrorInfo` and `x-flex-azure-guardrails-*` | `ContentSafetyBlocked` |
| `pii_block` | `403` with synthetic findings: v1 ambiguous `ErrorInfo`, v0.3 string `error.data` | `ToolInvocationError`, not `PIIDetected` |
| `request_limit` | Bare `429` with captured `x-token-*`, no `retry-after` | `TokenBudgetExceeded` |

On ingress, `request_limit` counts requests as a **replay trigger**, not a token
policy implementation. The configured limit/window decides when the capture is
returned; captured header values stay `1` limit, `1` remaining and `60000` ms to
reset, even if your local counter or clock differs. Passing calls gain no rate
headers. Test-only accepted v0.3 and v1 send captures record individual windows,
not a local accounting or passing-call replay contract. The
`donkey mock` request counter and headers described above are unchanged.

The response ID stays captured (v1 string `"1393"`, v0.3 PII numeric `1393`),
not your incoming request ID.
Bodies are not reserialized; only transport framing and the existing safe-header
filter differ from the capture. Streaming refusals are HTTP JSON or empty
responses, not SSE events. Other calls and passing streams retain the agent's
own bytes when supported, with the honesty header added. `budget` and `ciba`
are not ingress scenarios. No outbound A2A or authorization-code simulation is
provided.

The tested stock v1 `a2a-sdk` 1.2.2 JSON-RPC transport generates UUID request IDs,
then raises `A2AClientError` caused by `httpx.HTTPStatusError` for these send and
stream refusals before RPC decoding or ID checks. This is HTTP refusal evidence,
not a guarantee of successful correlation for other clients. The table lists
DDK classifications; the stock SDK does not raise those DDK types.

## Next steps

- [Local simulator](https://docs.donkey-kit.dev/simulator.md): model-route sentinels, scenarios and the server.
- [A2A gateway policies](https://docs.donkey-kit.dev/a2a/gateway-policies.md): what each A2A policy does.
- [Call other agents](https://docs.donkey-kit.dev/a2a/call.md): the `donkey.a2a` facade these faults exercise.

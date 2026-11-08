# Local simulator

Status: Live. Shipped, and verified against a real governed gateway.

Nobody can make the production gateway emit a PII block on cue, so the branch
of your agent that handles `PIIDetected` usually runs for the first time on a
real customer's data.

`donkey mock` fixes that. It is a local HTTP server that behaves like the
governed LLM proxy *for the failure paths*: it replays captured rejection
fixtures on demand and a captured happy-path completion for everything else.

```bash
pip install "donkey-kit[cli,local]"
donkey mock --port 8080        # --host defaults to 127.0.0.1
```

Point **any** client at `http://localhost:8080` — the SDK, a stock OpenAI
client, or cURL. To trigger a specific rejection, set the request's **`model`**
to the sentinel `donkey-sim/<shape>`:

```bash
# no /v1 segment — the governed proxy (and the simulator) has none
curl -s http://localhost:8080/responses \
  -H 'content-type: application/json' \
  -d '{"model": "donkey-sim/pii-detected"}' -i | head -1
# HTTP/1.1 403 Forbidden      (byte-identical to the captured PII block)
```

The selectable shapes are `token-rate-limit`, `pii-detected`,
`injection-protection`, `regex-prompt-guard`, `content-safety`,
`content-moderation`, `agent-killed`, `model-not-found`, `upstream-5xx`,
`client-id-missing`, `model-not-routable`, `model-missing`, `request-rate-limit`,
`ciba-pending`, and `ciba-denied` — the ten documented rejections (including the
request-rate-limit `429`, `RequestRateLimitExceeded`) plus the consumer-auth `401`, the
gateway's two `400`s for a model it cannot route (a bare model name on a multi-provider
proxy, and a request with no model; both `ModelNotRoutable`), and the OAuth2 OBO policy's two
CIBA step-up answers (`GatewayApprovalPending`, `GatewayApprovalDenied`). One happy-path
variant is selectable the same way: `donkey-sim/success-semantic` replays the
captured **semantic-routing** `200` (`routing_type == "Semantic"`), so
`donkey.last_call.matched_topic` and `routing_score` light up offline (see
[Gateway identity](https://docs.donkey-kit.dev/examples/general/gateway-identity.md#semantic-routing-the-matched-topic-and-score)).
Two more replay a proxy whose vendors are configured as `groupId:vendor`:
`donkey-sim/success-qualified-vendor` (a matched route) and
`donkey-sim/fallback-qualified-vendor` (the routing fallback). Both report
`x-llm-proxy-llm-provider: mulesoft:openai`, which `last_call.served_provider` keeps as is.
Any other `model` value gets the default model-based happy path. The
`donkey-sim/` prefix is a simulator-only control surface; the real gateway never
interprets it.

Because each body is the *same fixture* the SDK's error classifier is tested
against, a sentinel request surfaces in your agent as the typed exception — a
[`PIIDetected`](https://docs.donkey-kit.dev/errors.md), not a raw `403`.

## Routes

The simulator serves the two OpenAI-format model routes and the two native
ingresses:

| Route | Non-streaming `200` | Streaming |
|---|---|---|
| `POST /responses` | the captured OpenAI-upstream `200` | `"stream": true`: the captured SSE sample (one `response.created` event, as captured) |
| `POST /chat/completions` | the captured Azure OpenAI `chat.completion` `200` | `"stream": true`: a complete `chat.completion.chunk` stream ending in `data: [DONE]` |
| `POST /v1/messages` | the captured native Anthropic `message` `200` | `"stream": true`: the captured Anthropic SSE stream |
| `POST /models/{model}:generateContent` | the captured native Gemini `200` | `:streamGenerateContent`: the captured Gemini SSE stream |

The streamed `/chat/completions` body follows OpenAI's public chunk format. It
is not a gateway capture: no streamed `/chat/completions` response has been
captured yet. The two native routes replay a `Format=Anthropic` and a
`Format=Gemini` proxy on `model-based-routing` 1.0.5, so `donkey.last_call`
reports the served provider and model. They are the offline target for
`donkey.anthropic.client()` and ADK's native Gemini model.

On the native routes:

- A body that is not valid JSON gets the captured `400`
  `{"error":"Invalid JSON body: …"}`.
- Gemini reads the sentinel from the model in the path, and serves every other
  path model as `gemini`, as the live proxy does.
- `donkey-sim/anthropic-fallback` replays the Anthropic policy fallback
  (`last_call.fallback` is `True`).
- `model-not-routable`, `model-missing` and `model-not-found` are not sentinels there, because
  a native ingress serves an unresolvable model on its own vendor.

All four model routes share the `donkey-sim/`
sentinel, the scenarios below, one shared budget window, and the
`x-donkey-simulator` header. Any other route answers `404`.

## MCP tool fault injection

New in 0.2.1, `donkey.simulate_mcp()` makes
your agent's MCP tool calls hit a captured gateway refusal, with no gateway and
no Exchange. It is the MCP counterpart of `donkey.simulate(PIIDetected)`, which
still targets model calls only.

You declare the servers and tools the test needs. Inside the block,
`donkey.tools.discover()` returns those servers instead of reading Exchange, so
your agent's own discovery code runs unchanged:

```python
servers = {"acme/crm-tools/1.0.0": [{"name": "lookup_customer"}]}

with donkey.simulate_mcp("pii-request", servers=servers):
    tools = await donkey.tools.discover()
    agent = build_agent(await tools.langgraph())
    await agent.ainvoke(...)  # the first tool call is refused as a real PIIDetected
```

- `servers` maps each `group_id/asset_id/version` ref to its MCP tool
  descriptors. A descriptor without `inputSchema` gets an empty object schema.
  Declared servers are marked governed by a `simulated` check, and
  `discover()`'s filters are not applied to them.
- `times=1` (the default) counts injected requests. After that, a `tools/call`
  is answered by `call_tool(name, arguments)`. If you pass no `call_tool`, the
  answer is a single text item.
- Bind inside the block. After the block exits, those bindings refuse every
  request instead of reaching a network. Blocks do not nest.
- The model client is untouched. Combine it with `donkey.simulate(...)` to
  inject into both.

The capture names, the bindings that are supported and the behavior below are
the same as for the low-level helper.

### Low-level helper for a hand-built ToolSet

`donkey_kit.simulator.mcp.simulate_mcp()` injects a named captured refusal into
one `ToolSet` you already have, with a baseline you provide. Bind and connect
inside the context; bindings made before entry are not intercepted.

```python
import httpx
from donkey_kit.simulator.mcp import simulate_mcp

# tools is an already-resolved, governed ToolSet. offline_backend is your
# httpx request handler for MCP initialization, catalog reads and normal calls.
with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(offline_backend)):
    bound = await tools.langgraph()
    await bound[0].ainvoke(arguments)
```

The required `httpx.MockTransport` baseline handles all unmatched traffic. It
must implement your test backend's normal responses; this feature does not
provide a full MCP server or mock Exchange discovery. There is no network
fallback, environment-proxy routing or cross-endpoint redirect. Existing
governance, endpoint-provenance and requester checks are not bypassed.

| Capture name | Matching method | Existing behavior |
| --- | --- | --- |
| `pii-request` | `tools/call` | HTTP 403 JSON, request-phase `PIIDetected` |
| `pii-response-sse` | `tools/call` | HTTP 403 JSON, response-phase `PIIDetected`, **not an SSE error event** |
| `abac-denied`, `global-access` | `tools/call` | HTTP 200 access-denial error, `ToolNotInProfile` |
| `schema-invalid-args` | `tools/call` | Ordinary ambiguous JSON-RPC error; no schema-specific type |
| `poisoning-block-list` | `tools/list` | Captured SSE catalog error; no poisoning-specific type |

`times=1` counts matching RPC requests across this scope's clients, not
initialization, notifications or unrelated methods. Calls after that count use
the offline baseline. The selector is method-wide: it does not match tool names
or arguments, and captured diagnostics can name a different tool than your test.
Nesting restores the outer factory. Filtered and approval-derived sets share
the scope; unrelated ToolSets and model calls are unaffected. Do not overlap
independent simulation contexts on the same ToolSet owner.

Every injected response has `x-donkey-simulator: true`. Status, semantic headers,
content type and error payload come from the capture, but the response ID is
adapted to the outgoing request so native MCP sessions can correlate it. JSON
is reserialized, including the SSE data envelope: this is **not byte-verbatim
replay**. Original packaged files remain unchanged.

Native binding coverage is LangGraph, LlamaIndex, Anthropic, Microsoft Agent
Framework, the OpenAI Agents SDK and Strands; other bindings refuse before connecting.
Offline Agent Framework injection requires stable `agent-framework-core>=1.24.0`
and `mcp>=2.3.0`.
OpenAI Agents SDK injection requires stable `openai-agents>=0.20.0` and `mcp>=2.3.0`.
Strands injection requires stable `strands-agents>=1.64.0` and `mcp>=2.3.0`.
Older or prerelease stacks raise `ConfigError` before authentication or connection.
This restriction applies only to simulation, not normal production bindings.
LlamaIndex URLs selecting its legacy SSE
transport (`/sse`, `/sse/` or `transport=sse`) also refuse because that transport
ignores the injected client. A captured HTTP failure can end Anthropic's
persistent MCP session and be raised again during context teardown. Wrap the
whole binding context in `typed_refusals()` and reconnect before a later call.
Keep sessions inside the simulation context. Clients retained past exit fail
closed instead of reconnecting to the gateway. Raw exception responses and
native framework diagnostics remain sensitive.

For Agent Framework, wrap native invocation in `typed_refusals()` and use each
server object as an async context manager. Simulation owns a fresh HTTP client
for each connection and closes it when that connection ends; the offline baseline
remains yours. If you cancel `connect()`, its framework-owned connection attempt
can continue until the offline backend finishes. The framework then cleans up a
new connection whose caller was cancelled; await `tool.close()` to wait for cleanup
deterministically before leaving the simulation context. Cancellation does not
guarantee immediate cleanup. Whole-server approval and collision-renaming
limitations still apply.

```python
from donkey_kit import typed_refusals

with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(offline_backend)):
    bound = await tools.agent_framework()
    with typed_refusals():
        async with bound[0] as server:
            await server.functions[0].invoke(arguments=arguments)
```

For byte-verbatim HTTP replay without native session correlation, use the
packaged captures below. Neither form establishes new live gateway evidence.

For the OpenAI Agents SDK, connect each server inside the simulation scope and
wrap native calls in `typed_refusals()`. The framework owns and closes a fresh
HTTP client per connection, including failed initialization and cancelled
connection attempts; the offline baseline remains yours. Its default zero
retries, filtering and whole-server approval/collision limitations are unchanged.

```python
with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(offline_backend)):
    bound = await tools.openai_agents()
    with typed_refusals():
        async with bound[0] as server:
            await server.call_tool("calculate", arguments)
```

For Strands, bind inside the scope and enter each native `MCPClient` as a
synchronous context manager. Its background thread uses a fresh scoped HTTP
client on every connection, including an in-scope restart. The transport closes
that client; the offline baseline remains yours. A synchronous baseline handler
can run on the native thread, so make shared test state thread-safe.

```python
with simulate_mcp(tools, "pii-request", baseline=baseline):
    bound = await tools.strands()
    with bound[0] as client:
        result = client.call_tool_sync("test-call", "calculate", {})
        assert result["status"] == "error"
        assert client.call_tool_sync("next-call", "calculate", {})["status"] == "success"
```

Strands returns generic `status: error` tool results for the captured PII,
access-denial and schema failures. Structured policy fields and typed exceptions
are lost, so `typed_refusals()` cannot recover them and DDK never infers a policy
from the text. The poisoning capture instead raises the native `MCPError` from
`list_tools_sync()`; it remains ambiguous. Filtering and whole-server
approval/collision restrictions are unchanged.

Keep the native context inside the simulation scope. Retained tool calls fail
after scope exit and cannot reach the baseline or gateway; cached descriptors
can still be inspected. Close the native session even after a failure. On the
tested Strands 1.64.0 / MCP 2.3.0 stack, stopping failed, cancelled or expired
sessions can emit an upstream `RuntimeWarning` that
`MCPClient.stop.<locals>._set_close_event` was never awaited, although the HTTP
clients close and background threads exit. DDK does not patch or suppress this
warning. Bound native-thread tests with a subprocess deadline so an upstream
cleanup regression cannot hang your test suite.

### Reusable offline baseline

Use `mcp_baseline()` when you need initialization, tool discovery and ordinary
tool results without writing an HTTP handler. It returns a caller-owned
`httpx.MockTransport` for one exact endpoint. Supply MCP tool descriptors and a
synchronous `call_tool(name, arguments)` handler returning an MCP `CallToolResult`
object as a dictionary:

```python
from donkey_kit.simulator.mcp import mcp_baseline, simulate_mcp

endpoint = tools.servers[0].endpoint_url
baseline = mcp_baseline(
    endpoint,
    server_protocol_version="2025-06-18",
    tools=[{
        "name": "calculate",
        "description": "Return an offline result",
        "inputSchema": {"type": "object", "properties": {}},
    }],
    call_tool=lambda name, arguments: {
        "content": [{"type": "text", "text": "success"}],
    },
)

with simulate_mcp(tools, "pii-request", baseline=baseline):
    bound = await tools.agent_framework()
    with typed_refusals():
        async with bound[0] as server:
            await server.functions[0].invoke(arguments={})
```

`server_protocol_version` controls the server's `initialize` response; it defaults
to `2025-06-18`. It does not change the client's requested version. The native MCP
SDK accepts or rejects that response, so an unsupported string is useful for
negative negotiation tests. This does not change package-version requirements,
translate captures, or establish conformance of a complete server to that version.

The helper is a stateless JSON-response baseline, not a full MCP server simulator.
It supports initialization, tool listing/calls, initialized notifications and ping.
Unknown tools/invalid arguments envelopes return JSON-RPC `-32602`; unknown methods
return `-32601`. It does not validate arguments against `inputSchema`, implement
policies, SSE streams, resources, prompts or session IDs. Handler exceptions
propagate to your test; handlers must not perform network calls. For richer behavior
or multiple endpoints, continue supplying your own `httpx.MockTransport`.

## Packaged MCP captures

Separately, the package data contains **15 captured MCP responses**: 13 policy
envelopes in `anypoint/mcp_policies` and two access-denial header/body pairs in
`anypoint/mcp_access`. These captures are new in 0.2.1. Use `donkey_kit.simulator.fixtures.fixture_bytes()` to read
them without depending on a repository checkout's test files.

This is raw offline replay, not a `donkey mock` route or a `donkey-sim/` sentinel,
and not bound-tool `simulate()` injection. There is no session negotiation or
request-ID rewriting: captured JSON-RPC IDs and SSE framing stay unchanged.
It tests handling of a selected response, not policy enforcement or end-to-end
framework behavior.

The example decodes the `pii-request.json` capture envelope, then encodes its
`body` string as UTF-8; the response body is not JSON-reserialized. Replay only
the safe headers from `replay_headers()`, restore `content-type`, and add
`x-donkey-simulator: true` to the replay, never to the capture. The URL is a
reserved example address, not a real endpoint; the explicit `MockTransport`
handles the call entirely offline, with no server or credentials.

```python
import json

import httpx

from donkey_kit import PIIDetected, jsonrpc_refusal
from donkey_kit.simulator.fixtures import Fixture, fixture_bytes, replay_headers

capture = json.loads(fixture_bytes("anypoint/mcp_policies", "pii-request.json"))
fixture = Fixture(
    shape="mcp-capture:pii-request",
    status=capture["status"],
    headers=capture["headers"],
    body=capture["body"].encode("utf-8"),
    content_type=capture["headers"]["content-type"],
)

def respond(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        fixture.status,
        headers={
            **replay_headers(fixture),
            "content-type": fixture.content_type,
            "x-donkey-simulator": "true",
        },
        content=fixture.body,
    )

with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:
    response = client.post("https://gateway.example/mcp/")

assert response.content == fixture.body
assert response.headers["x-donkey-simulator"] == "true"
refusal = jsonrpc_refusal(response.json(), response=response)
assert isinstance(refusal, PIIDetected)
assert refusal.phase == "request"
assert refusal.response is response
```

For access-denial pairs, read the header file with `fixture_bytes()` and use
`parse_status()` and `parse_headers()` from the same module; read the body file
as bytes. Schema and poisoning errors cannot safely identify a gateway policy
from generic codes or diagnostic text, so `jsonrpc_refusal()` leaves those
captures untyped. Despite its name, `pii-response-sse.json` is HTTP 403
`application/json`, not an SSE error event. The matcher takes a parsed JSON-RPC
envelope, not raw SSE bytes. See [MCP gateway policies](https://docs.donkey-kit.dev/tool-access/gateway-policies.md)
for the evidence boundaries and framework-specific refusal behavior.

## Choosing a port or host

`donkey mock` binds `127.0.0.1:8080` by default. Two flags move it:

| Flag | Default | Meaning |
|---|---|---|
| `--port` | `8080` | TCP port to bind. Pass another value if `8080` is in use. |
| `--host` | `127.0.0.1` | Interface to bind. `0.0.0.0` exposes it beyond localhost. |

```bash
donkey mock --port 9000 --host 0.0.0.0
```

  Binding `0.0.0.0` makes the simulator reachable from other machines on your
  network. It is a fixture replay that enforces no policy and ignores
  credentials, so only expose it on a network you trust.

Point your client at the same address: set
`DONKEY_LLM_PROXY_URL=http://127.0.0.1:<port>` (for the example above,
`http://127.0.0.1:9000`). The SDK accepts plain `http://` for loopback hosts
(`localhost`, `127.0.0.0/8`, `::1`); `http://0.0.0.0:…` is refused. To reach the
simulator from another machine or container by LAN address or hostname (for
example `http://simulator:9000`), set `DONKEY_ALLOW_HTTP=1` in that client's
environment; the SDK then emits a `ConfigWarning` naming the endpoint. See
[Endpoints must use `https://`](https://docs.donkey-kit.dev/reference/configuration.md#endpoints-must-use-https).

  Behind an HTTP proxy (`HTTP_PROXY` or `ALL_PROXY` set, as on many corporate
  machines and CI runners), the client sends loopback requests to the proxy
  too, and the proxy usually cannot reach your simulator. Add the simulator's
  host to `NO_PROXY`, for example `NO_PROXY=127.0.0.1,localhost`.

The simulator's port is **not** read from an environment variable or
`.donkey-kit.toml` — the `--port`/`--host` flags (or the `serve()` keyword
arguments below) are the only way to change it.

Set the simulator URL in the environment (or in code), as above. If you keep it
in `.donkey-kit.toml` instead, put the credentials in `.donkey-kit.local.toml`
next to it: a URL from the working directory's config files, loopback included,
only receives credentials from those files. See
[Which credentials a URL receives](https://docs.donkey-kit.dev/reference/configuration.md#which-credentials-a-url-receives).

  If you run the simulator's ASGI app under `uvicorn` yourself instead of
  through `donkey mock`, remember uvicorn's own default port is `8000`, not
  `8080`. Using `donkey mock` (or `serve()`) gives you the documented `8080`
  default.

### Running the simulator from Python

`donkey mock` is a thin wrapper over `serve()`. To boot the simulator from your
own code, call it directly (the `[local]` extra must be installed):

```python
from donkey_kit.simulator import serve

serve(host="127.0.0.1", port=9000)
```

`serve(*, host="127.0.0.1", port=8080, config=None)` is keyword-only and blocks
until the process is stopped. Pass a `SimulatorConfig` (also importable from
`donkey_kit.simulator`) as `config=` to script the same fault-injection
scenarios the `--scenario` flag builds. If the `[local]` extra is missing,
`serve()` raises `ImportError`.

## Budget windows on the happy path

Happy-path `200` responses carry a synthesised `x-llm-proxy-ratelimit` budget
window that decrements on every call, so the pacing logic from
[Budget & pacing](https://docs.donkey-kit.dev/budget.md) runs end-to-end against the simulator. For a real
windowed counter that resets and emits the `429` on exhaustion, use the
[`budget` scenario](#scenario-scripting). The request window
(`donkey.budget.requests`) is filled only by the
[`request_limit` scenario](#request_limitlimitrequestswindowduration).

## Scenario scripting

The sentinel lets *you* decide which call fails. A **scenario** lets the
simulator decide, on a rule you set once at boot, so failures fire on their own
and deterministically across a whole run. Pass `--scenario` once per rule; the
grammar is `<name>:<key=value,key=value>`.

```bash
donkey mock --port 8080 \
  --scenario pii_block:every=5 \
  --scenario budget:limit=20000,window=60s \
  --scenario request_limit:limit=3,window=60s \
  --scenario injection:on-pattern="ignore previous"
```

When more than one is set, they are evaluated `injection` → `pii_block` →
`request_limit` → `budget`, and the first that fires wins. The `donkey-sim/<shape>` sentinel
takes precedence over all of them — it is an explicit "force this exact shape"
override.

  The sentinel is for one-off failures; a scenario runs across a whole session.
  Use both together to cover specific calls and background failure rates.

### `pii_block:every=N`

Serves the captured `pii-detected` **403** on every Nth model call, on any model
route; the
other calls get the happy path.

```bash
donkey mock --scenario pii_block:every=5
```

Every fifth call raises a typed [`PIIDetected`](https://docs.donkey-kit.dev/errors.md) in your agent, so your
masking logic runs in your own terminal before the pull request is opened.

### `budget:limit=<tokens>,window=<duration>`

Runs a **real, wall-clock-windowed token counter**. Shrink an hour-long window
to a minute and your [pacing and resume logic](https://docs.donkey-kit.dev/budget.md) is exercised in ninety
seconds.

```bash
donkey mock --scenario budget:limit=20000,window=60s
```

Each call deducts the happy-path completion's reported token usage (override
with `cost=<tokens>`). While budget remains, the `200` carries the
`x-llm-proxy-ratelimit` prose header, decreasing as you spend. Once the window
is spent, calls receive the captured `token-rate-limit` **429**, with
`x-token-remaining` and `x-token-reset` recomputed from the counter and the
milliseconds left in the window, until the window rolls over. `duration`
accepts `ms`, `s`, or `m` (a bare number is seconds).

  The numeric `x-token-*` headers appear only on the `429`, and the prose
  `x-llm-proxy-ratelimit` header only on the `200` — matching the real gateway.
  The simulator never emits a header shape the gateway does not.

### `ciba:pending=[,then=approve|deny]`

Plays the OAuth2 OBO policy's CIBA step-up. The first `N` calls get the captured `401`
`authorization_pending` (a typed [`GatewayApprovalPending`](https://docs.donkey-kit.dev/errors.md), with its
`retry_after`). Then the user "answers": with `then=approve` (the default) every later call
goes through, and with `then=deny` it gets the `403` `access_denied`
([`GatewayApprovalDenied`](https://docs.donkey-kit.dev/errors.md)).

```bash
donkey mock --scenario ciba:pending=2,then=approve
```

The count is the scenario's own. The simulator does not model the live policy's per-user
cache or its silent expiry, so test your own deadline separately. The two answers were
captured on an A2A `message/send` and an MCP `tools/call`, not on an LLM proxy route; the
simulator serves the same bytes on its model routes so your error handling can be tested
offline.

### `request_limit:limit=<requests>,window=<duration>`

Runs a **request counter** for the gateway's `rate-limiting` policy, which
counts requests rather than tokens. It composes with the `budget` scenario.

```bash
donkey mock --scenario request_limit:limit=3,window=60s
```

Each `200` carries the `x-ratelimit-limit` / `-remaining` / `-reset` trio from
the live counter, so `donkey.budget.requests` and
[`pace()`](https://docs.donkey-kit.dev/budget.md) run end-to-end. Once the window is spent, calls receive the
captured request-rate-limit `429`, a typed
[`RequestRateLimitExceeded`](https://docs.donkey-kit.dev/errors.md). The window is fixed: it starts when the
simulator starts and rolls every `window`; the first call does not start it.
`duration` takes the same units as `budget` (`ms`, `s`, `m`; a bare number is seconds).

### `injection:on-pattern=<substring>`

Serves the `injection-protection` **400** on any request whose `input`,
`messages`, or `instructions` text contains the substring (case-insensitive).

```bash
donkey mock --scenario injection:on-pattern="ignore previous"
```

The request surfaces as a typed [`PromptInjectionBlocked`](https://docs.donkey-kit.dev/errors.md) (via the
`x-injection-protection: blocked` discriminator), so the refusal branch of a
guardrailed bot is exercised before it faces a real attack.

## Outbound A2A fault injection

`donkey.simulate_a2a()` is the A2A counterpart of
[`simulate()`](https://docs.donkey-kit.dev/testing.md) and
[`simulate_mcp()`](#mcp-tool-fault-injection). It makes an agent you **call**
through [`donkey.a2a`](https://docs.donkey-kit.dev/tool-access/a2a.md) get a captured gateway refusal, with
no gateway and no Exchange. Like `donkey.a2a`, it is provisional.

```python
import pytest
from a2a.types import Message, Part, Role, SendMessageRequest
from donkey_kit import Donkey, ToolInvocationError

request = SendMessageRequest(
    message=Message(message_id="m1", role=Role.ROLE_USER, parts=[Part(text="Email a@b.c")])
)

async def test_delegation_handles_a_pii_block():
    async with Donkey.from_env() as donkey:
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

| `capture` | `version="1.0"` | `version="0.3"` | Raised error |
|---|---|---|---|
| `pii-send`, `pii-stream` | yes | `pii-send` only | `ToolInvocationError` |
| `azure-block-send`, `azure-block-stream` | yes | no | `ContentSafetyBlocked` |
| `rate-send`, `rate-stream` | yes | `rate-send` only | `TokenBudgetExceeded` |

The raised error is whatever the existing classification gives each packaged
Omni Gateway capture, the same bytes listed in
[A2A gateway policies](https://docs.donkey-kit.dev/a2a-gateway-policies.md). The rate-limit captures have
no body, so the error carries headers only. This covers agents you call. For
an agent you **serve**, use the ingress replay below.

## A2A ingress replay

See [A2A gateway policies](https://docs.donkey-kit.dev/a2a-gateway-policies.md) for the version-specific
capture inventory. Local replay verifies handling, not gateway enforcement.

[`donkey dev`](https://docs.donkey-kit.dev/a2a.md#simulated-ingress) wraps your served agent rather than the
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

## How the simulator works

  **Every simulator response carries `x-donkey-simulator: true`** — including
  framework-generated `405`/`500` responses — so a simulated response is never
  confused with a real gateway in a log, a trace, or a screenshot.

- **It replays; it does not evaluate policy.** The simulator tests how your
  agent handles a refusal, never *which* prompts get refused. You choose the
  refusal (the `donkey-sim/<shape>` sentinel or a `--scenario` rule); the
  simulator never inspects a prompt and decides it violates a policy, and it
  ignores authentication. Testing whether a prompt would be blocked by your
  deployed policy configuration requires the real gateway.
- **It works with any client.** A stock, non-SDK client — plain `httpx` or
  `openai.OpenAI(base_url="http://localhost:8080", …)` — receives the
  byte-identical rejection bodies and exact discriminator headers.
- **Its fixtures are the SDK's test fixtures.** The simulator serves the same
  files the error classifier is tested against, so the simulator and the
  [error taxonomy](https://docs.donkey-kit.dev/errors.md) cannot drift apart. The wheel ships a sha256
  integrity manifest of every fixture, so a changed byte fails loudly rather
  than silently altering what the simulator replays.
- **It is pure Python — not Omni/Flex Gateway running in Local Mode.**
  `pip install "donkey-kit[local]"` adds Starlette and Uvicorn; no Docker
  required. DDK does not support Local Mode as a deployment target (see the
  [unsupported boundary](https://docs.donkey-kit.dev/reference/unsupported-boundary.md)); real-gateway
  behavior is exercised against a hosted (Connected Mode) proxy instead.

## Related

- [Simulated ingress](https://docs.donkey-kit.dev/a2a.md#simulated-ingress) — `donkey dev`, which replays
  method-specific A2A captures on inbound calls to your agent.
- [Testing & conformance](https://docs.donkey-kit.dev/testing.md) — `simulate()` for in-process unit tests,
  and the `gateway` pytest fixture that runs this simulator on an ephemeral port.
- [Error taxonomy](https://docs.donkey-kit.dev/errors.md) — the typed exceptions each shape maps to.
- [CLI](https://docs.donkey-kit.dev/cli.md) — the full `donkey` command reference.

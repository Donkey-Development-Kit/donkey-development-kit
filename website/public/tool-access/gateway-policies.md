# MCP gateway policies

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

Gateway policies can remove or rename tools, replace a catalog with meta-tools,
reject a call, or transform its result. Configure enforcement on the MCP API
instance in API Manager; DDK consumes the gateway-visible contract rather than
reimplementing the policies in your agent.

  **Partial evidence, not an end-to-end policy certification.** Gateway captures
  establish the wire behavior described below. Typed PII surfacing across
  frameworks is checked by local capture replay, not live framework calls to
  a policy-enabled gateway. Ordinary discovery and binding have separate live
  checks. A captured refusal does not establish every configuration of a policy.

To exercise response handling offline, use the
[packaged MCP captures](https://docs.donkey-kit.dev/simulator.md#packaged-mcp-captures) with raw `httpx.MockTransport`.
This is separate from model-route simulation and does not establish a live
framework round-trip.

## MCP Support

Start with [MCP Support](https://docs.mulesoft.com/gateway/latest/policies-included-mcp-support)
on the gateway instance. The captured policy chain used MCP Support `1.1.1`
on Omni Gateway `1.14.4`, speaking MCP `2025-06-18` over streamable HTTP.
DDK uses the framework's MCP client; it does not implement another MCP protocol.

Use the resolved endpoint with its trailing slash and the instance's consumer
credential, as described in [Framework binding](https://docs.donkey-kit.dev/tool-access/binding.md#auth).
In the captured deployment, attaching a policy in API Manager alone did not
activate it: the instance needed redeployment. Ask the instance owner to check
the deployed policy chain before diagnosing a client failure.

## MCP Global Access

This policy controls which tools a consumer may call. A denied tool is omitted
from `tools/list`; a stale descriptor can still lead to a refused `tools/call`.
The captured refusal is HTTP 200 with JSON-RPC code `-32008` and message
`"Unable to access"`, not an HTTP authentication failure.

DDK recognizes this specific captured pair as `ToolNotInProfile`. Its
`.tool_name` names the denied tool, while `.profile` is `None`: the gateway
does not identify the profile or rule. Ask the instance owner to grant access
or stop offering that tool. Retrying the unchanged call does not fix access.

## MCP Attribute-Based Access Control

ABAC makes the same tool-list and call decisions per caller. The captured
denial uses the same `-32008` / `"Unable to access"` pair as Global Access,
so `ToolNotInProfile` does not distinguish which of the two policies denied it.
Configure the caller's access rules at the gateway, not with a client-side filter.

LangGraph, LlamaIndex and Anthropic bindings raise `ToolNotInProfile` with the
server's coordinates. For raised structured errors from OpenAI Agents, Agent
Framework and CrewAI, `typed_refusals()` recovers it without server coordinates.
ADK and Strands return error text with the code lost; DDK leaves that text alone.
See [Tools outside your access profile](https://docs.donkey-kit.dev/tool-access/binding.md#tools-outside-your-access-profile).

## MCP PII Detector

Captured Reject responses are **HTTP 403 JSON**. Request rejection has
code `-32602` and `phase="request"`; response rejection has code `-32603` and
`phase="response"`. Response rejection was captured with both SSE and JSON-only
upstreams; each rejection is JSON, not an SSE error event. The JSON-only capture used Email Reject
on a fixed synthetic upstream response with no PII input; it adds wire evidence,
not a live SDK/framework round-trip or a packaged replay fixture.
DDK types these as `PIIDetected` only when both exact
markers `data.policy="mcp-pii-detector"` and
`data.reason="policy_pii_detected"` accompany the matching phase/code pair.

| Binding | How the captured PII refusal reaches you |
| --- | --- |
| LangGraph, LlamaIndex, Anthropic | `PIIDetected` from the per-tool wrapper |
| OpenAI Agents, Agent Framework | `PIIDetected` through `typed_refusals()` or `donkey.run()` when the structured error survives |
| ADK, Strands | Error text returned; policy metadata is lost, so no inferred typed refusal |
| CrewAI | The locked MCP 1.x tool future does not settle on the captured HTTP 403; this PII path is unsupported |

Wrap the framework invocation, not discovery, when using the shared bridge:

```python
from donkey_kit import PIIDetected, typed_refusals

try:
    async with typed_refusals():
        result = await server.call_tool("lookup_customer", arguments)
except PIIDetected as exc:
    print(exc.phase, exc.entities, exc.remediation)
    # Redact the input or review the gateway's response policy before a new call.
```

Here `server` is an already-connected native MCP server object, and `arguments`
is your tool's argument mapping. The example does not supply a new endpoint or
retry the blocked call.

`.phase`, `.entities` and `.findings` are safe projected metadata. Findings
retain only canonical PII types and entirely star-masked values capped at 256
characters; custom labels and unknown or malformed findings are discarded.
`.gateway_message` is `None`. Treat `.response`, `.framework_error` and suppressed
exception context as raw, sensitive diagnostics, not safe logging fields.
See [MCP PII metadata](https://docs.donkey-kit.dev/errors.md#mcp-pii-metadata).

Log-and-mask was observed changing SSE text using a synthetic pattern; it is a
transformed result, not `PIIDetected`. Elicitation decline remains unverified.
DDK does not implement MCP elicitation recovery.

## MCP Schema Validation and Tool Poisoning

The captured invalid-argument and missing-id responses use HTTP 200 JSON-RPC
errors (`-32602` and `-32600`). The poisoning BlockResponse capture uses HTTP
200 SSE with `-32603` and descriptor-drift findings; RemoveTool leaves an empty
tool list. These observations do not prove a malicious server was detected.

These errors lack reliable structured policy markers. DDK does not infer a
schema or poisoning refusal from generic codes or diagnostic text. Check your
arguments against the gateway-visible schema and ask the instance owner to
review schema/descriptor drift. Do not log the raw `data`: it can echo rejected
values. Empty catalogs can have several causes, not just poisoning removal.

For a **parsed JSON-RPC error envelope**, `jsonrpc_refusal(envelope)` returns
a recognized policy refusal or `None` for an ordinary or ambiguous error.
It does not parse raw SSE bytes. Explicit `classify(response)` can classify a
JSON error response as a safe `ToolInvocationError` when ambiguous, but a
successful HTTP 200 body is not automatically intercepted by the transport.
A tool result with `isError: true` is still a tool result, not a policy refusal.

Other explicit policy markers without a recognized built-in or custom rule
produce `PolicyViolation(policy="unknown")`, not a guessed schema or PII type.
HTTP authentication challenges, rate limits and server failures retain their
own classification. A typed policy refusal is terminal; do not automatically
retry it or turn a returned error-text result into a refusal by message matching.

## MCP Tools Progressive Disclosure

The captured default catalog lists `search_tools` and `invoke_tool` instead
of the upstream tools. Bind those meta-tools as listed: the gateway owns search
and nested invocation. `describe()` reports their joint presence at INFO level;
this is expected catalog information, not a warning or proof of installed policy.

A filter on a hidden upstream name does not restrict what `invoke_tool` may
invoke. An approval rule on `invoke_tool` gates every call to that meta-tool,
not one nested tool argument. Use gateway access policies for enforcement.
Invalid invoke parameters were captured as `-32602` with `reason="invalid_params"`,
which is not a policy discriminator. DDK adds no client-side search implementation.

## MCP Tool Mapping

The gateway-visible name is the name to filter, approve and invoke. A captured
mapping changed `calculate` to `arithmetic`; calling `arithmetic` succeeded.
`ToolSet.name_map` explains DDK's collision prefixes, not the gateway's renames.
A filter or approval rule naming a hidden upstream name raises `ConfigError`
when tools are listed. See [Gateway-visible catalogs](https://docs.donkey-kit.dev/tool-access/binding.md#gateway-visible-catalogs).

Discovery stays connection-free; `describe()` or binding reads the tool catalog.
Neither `donkey.tools.lock()` nor `discover(locked=True)` pins this catalog today:
both remain [blocked on verification](https://docs.donkey-kit.dev/tool-access/lockfile.md).

## MCP Payload Optimization

Supply `expected_info` when the tool's exposed input schema accepts it. DDK's
native bindings forward that argument unchanged; the gateway owns any extraction
or trimming. Local reference-server checks establish forwarding, not live
gateway optimization or a client-side trimming implementation.

The live capture covers HTML-to-Markdown conversion and whitespace collapse
in an SSE tool result. The upstream result remained `isError: true`, not a
typed refusal. JSON response-format coverage is partial; base64, null removal,
TOON and LLM trimming are not established by that capture. DDK has no TOON decoder.
Read the framework-native result as returned rather than assuming decoded JSON
or a plain string.

## Agent telemetry

Use the shared [agent telemetry](https://docs.donkey-kit.dev/telemetry.md) surfaces for runs, correlation and
governed model calls. Gateway policy configuration is not a new DDK telemetry
API, and this inventory does not promise per-policy MCP spans or attribution
headers on every framework-owned tool call. The
[binding auth contract](https://docs.donkey-kit.dev/tool-access/binding.md#auth) remains separate from LLM
proxy attribution.

## MCP Bridge

MCP Bridge's transcoding asset is `mcp-transcoding`. Its coverage here is
source-only: it is not live-verified and has no capture-backed fixture tests.
Do not treat ordinary live MCP binding checks as a verification of REST-to-MCP
transcoding. Consume a resolved governed MCP endpoint with the existing binding
API; DDK does not add a transcoder, provisioning API or bridge-specific client.

## Next steps

- [Discovery](https://docs.donkey-kit.dev/tool-access/discovery.md): resolve governed servers before connecting.
- [Framework binding](https://docs.donkey-kit.dev/tool-access/binding.md): choose native tool objects and manage their lifecycle.
- [Governed errors](https://docs.donkey-kit.dev/errors.md#json-rpc-refusal-matching): distinguish reliable refusal markers from raw diagnostics.

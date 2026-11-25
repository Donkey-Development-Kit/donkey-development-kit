# Gateway step-up & challenges

Status: Live. Shipped, and verified against a real governed gateway.

## Discovered tools Local round-trip

`@donkey.tool` decorates a local function. A governed MCP tool from
[`donkey.tools.discover()`](https://docs.donkey-kit.dev/tool-access/discovery.md) is not one, so you declare
its approval once on the `ToolSet` instead. The gate runs in the SDK, before
the call leaves the client. It has been run against local MCP servers, not
through a gateway (see [Framework binding](https://docs.donkey-kit.dev/tool-access/binding.md)):

```python
# `tools` is the ToolSet that donkey.tools.discover() returns, before you bind it.
tools = tools.require_approval(tools=["issue_refund", "void_invoice"], risk="financial")
```

A call to a gated tool behaves exactly like a gated `@donkey.tool`: it raises
`ApprovalRequired`, or pauses a LangGraph graph with `interrupt()`, records the
same span events, and resolves through `donkey.approvals`: the client whose
`donkey.run()` or `as_user()` block runs the call, else the client whose
`tools.discover()` built the set. `Approval.arguments`
holds the tool's arguments by name, taken from its input schema.

Schema defaults appear in the review snapshot, but only supplied fields are
sent in the MCP invocation; the server remains responsible for omitted defaults.
This behavior is explicitly selected for MCP schema invokers, not inferred
from a Python callable having `__signature__`.

| Keyword | Default | Meaning |
|---|---|---|
| `tools` | — | Exact tool names to gate. |
| `risk` | `None` | Advisory metadata for the reviewer and approval span events, as on `@donkey.tool`; not sent with MCP tool calls or used to request gateway step-up. |
| `timeout` | `"15m"` | How long a pending approval waits, as `approval_timeout` on `@donkey.tool`. |

- **Names are exact,** like [`filter()`](https://docs.donkey-kit.dev/tool-access/binding.md#mcp-session-management).
  A bare name such as `"issue_refund"` gates that tool on every server in the
  set. The server-prefixed name collision resolution exposes, such as
  `"billing__issue_refund"`, gates only that server's tool.
- **Per-tool bindings only.** LangGraph, LlamaIndex and the Anthropic SDK
  gate the call. The whole-server bindings (ADK, Strands, the OpenAI Agents
  SDK, Agent Framework, CrewAI) raise `ConfigError` for a set with a rule,
  because they bind a whole server as one object and there is no per-tool call
  to hold. Bind a per-tool framework, or use the framework's own approval; see
  [Whole-server bindings](https://docs.donkey-kit.dev/tool-access/binding.md#whole-server-bindings).
- **A typo fails at bind time.** Binding raises `ConfigError` for a name that no
  server in the set exposes, so a misspelt name can never leave a tool ungated.
  Removing a gated tool with `filter()` is fine.
- **Rules accumulate.** Each call returns a new `ToolSet`, `filter()` keeps the
  rules, and when two rules name the same tool the later one sets its `risk`
  and `timeout`.

The approval is advisory here too, for the same reason as above: the gateway
never learns that it was needed.

## Gateway step-up

The approvals above are DDK's, and they run in your process. The gateway has
its own. With CIBA enabled (`cibaEnabled`), the OAuth2 OBO Credential Injection
policy asks the user to approve the call out of band, outside your app, before
it exchanges their token, and holds the request until they answer. The gateway's
policy configuration decides when that happens. Your code cannot ask for it,
and `risk=` plays no part in it. DDK's part is to report the gateway's answer
for what it is, not as a bad credential:

| Gateway answer | `classify()` returns | What to do |
|---|---|---|
| `401`, flat `{"error":"authorization_pending"}`, `retry-after`, **no** `www-authenticate` | `GatewayApprovalPending`, with `retry_after` in seconds | Send the same request again after `retry_after`. It goes through once the user approves. Don't refresh the credentials: they were accepted. |
| `403`, flat `{"error":"access_denied"}` | `GatewayApprovalDenied`, a `PolicyViolation` with `policy="oauth2-obo-ciba"` | Tell the user the action was not authorized. It is never retried. |
| `502`, `{"error":"ciba_initiation_failed"}` | `UpstreamModelError`, as for any `5xx` | The gateway could not start the approval request. |

`GatewayApprovalPending` is a `DonkeyError`, not a `PolicyViolation`: nothing
has been refused yet.

`GatewayApprovalDenied` and `ApprovalRejected` share the base
`ApprovalError`, so one `except ApprovalError` catches both refusals. They stay
two types because different parties said no. `GatewayApprovalDenied` is the
gateway's own step-up, refused by the user out of band, and is also a
`PolicyViolation`. `ApprovalRejected` is a reviewer rejecting a DDK approval in
a framework pause, and no gateway was involved. Each rule needs its status and its body, so a plain `401`
or `403` is still an `AuthError`. See [Typed refusals](https://docs.donkey-kit.dev/errors.md) for the rest of
the taxonomy.

The policy's behaviour, as captured from OBO policy 1.1.3:

- **An unanswered request never expires for the client.** Past its expiry
  (about two minutes) the policy starts a new request and keeps answering
  "pending", so keep your own deadline.
- **A denial is cached.** The first retry after a deny can still be one more
  pending `401`. After that the gateway answers with the denial, cached per
  user rather than per conversation, for up to an hour. A retry, a fresh token
  or a new A2A `contextId` gets the same denial.

### Waiting while listing MCP tools

`ToolSet.describe()`, which every binding calls, raises the first
`GatewayApprovalPending` at once. A step-up answer is not treated as a refused
credential: the cached gateway credential is kept, and nothing is re-sent. To
wait for the user instead, build the set with `wait_for_gateway_approval()`:

```python
# `tools` is the ToolSet that donkey.tools.discover() returns, before you bind it.
tools = tools.wait_for_gateway_approval("2m")
```

The listing is then sent again after each `retry_after`, kept between 1 and 30
seconds, until it goes through or the user denies it, which raises
`GatewayApprovalDenied` at once. When the next pause would pass the timeout, the
last `GatewayApprovalPending` is raised. The timeout defaults to `"2m"` and must
be a positive duration, and the view keeps it through `filter()` and
`require_approval()`. The loop decides nothing: the user approves out of
band, the gateway lets the same request through, and the gateway keeps the
audit trail.

A set with several MCP servers lists them all at once. The first server to fail
stops the rest: when one server's approval is denied, its wait times out, or the
gateway refuses the credential, DDK cancels the other servers' waits first. Then
it raises that error, or, for a refused gateway credential, lists every server
once more with a fresh one. No server keeps re-sending a request the user would
have to answer. Cancelling the `describe()` call, or the binding that runs it,
cancels every server's wait the same way.

Keep the timeout at `"2m"` or less. The policy's approval request expires after
about two minutes, and the gateway then starts a new one, which asks the user
again. A longer timeout can therefore prompt the user more than once for the
same listing, so `wait_for_gateway_approval()` emits a `ConfigWarning`
(`donkey_kit.core.errors.ConfigWarning`) when you pass one.

### Step-up on a tool call

A tool *call* is never retried, and whether its step-up answer is typed depends
on the version of the `mcp` package installed with your framework, not on the
framework:

- **`mcp` 1.x** (LangGraph's `langchain-mcp-adapters` runs on it): the client
  raises an `httpx.HTTPStatusError`, and the HTTP client DDK hands the binding
  reads the error body. A tool from a per-tool binding (`langgraph()`,
  `llamaindex()`, `anthropic()`) then raises the gateway's answer typed, as in
  the table above: `GatewayApprovalPending` for the `401`,
  `GatewayApprovalDenied` for the `403`, and `AgentKilled` for a kill-switch
  `403`. So does any block under `donkey.run()`, `@donkey.governed` or
  `typed_refusals()`. Nothing is sent again: to wait for the user, call the
  tool again after `retry_after`.
- **`mcp` 2.x** (the current release, which most other frameworks install):
  the client turns any HTTP error into an `MCPError` with code `-32603`, with
  no status and no body, or into an error result. DDK cannot tell a step-up
  from any other failure, so it reaches your code as the framework's own error
  or result. Handle it where your framework lets you.

A `403` from the MCP server whose body matches none of these shapes stays the
framework's error. It is not turned into a generic `PolicyViolation`.

The typed `mcp` 1.x path is checked offline, against the captured gateway
answers. No framework client has been run against a live step-up on
`tools/call`.

  **A CrewAI tool call behind step-up hangs.** Against a local replay of the
  gateway's pending answer, CrewAI's MCP adapter thread stopped on the `401`
  and the call never returned. Run CrewAI only against MCP servers whose policy
  does not ask for step-up on `tools/call`.

`donkey mock --scenario ciba:pending=2,then=approve` plays the pending and
approved sequence offline (see [Local simulator](https://docs.donkey-kit.dev/simulator.md)).

## A2A Authorization-Code Challenges

The outbound A2A facade raises `GatewayAuthRequired` for the captured v0.3
In-Task Authorization Code challenge. Its frozen `.challenge` contains the
provider, authorization/token endpoints, scopes, audience, redirect URI,
response type, PKCE method and body encoding. `.task_id` and `.context_id`
are the IDs returned by the gateway, not assumed to be those sent.

This is separate from local tool approval and CIBA: do not resolve it through
`donkey.approvals` or poll it with `wait_for_approval()`. Your application owns
OAuth and must validate the untrusted challenge URLs before using them.
DDK runs no OAuth flow, hosts no redirect and performs no automatic resend.
Typing is tested against both captured JSON send/stream responses, not a live
OAuth completion; v1 challenge semantics and successful token resubmission
remain unverified. See [Authentication challenges](https://docs.donkey-kit.dev/a2a/call.md#authentication-challenges).

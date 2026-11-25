# Human-in-the-loop

Status: Live. Shipped, and verified against a real governed gateway.

Refunds over €100 need a human. Without a shared vocabulary that is bespoke
code in every team and every framework. With DDK you declare the requirement
on the tool itself:

```python
from donkey_kit import Donkey

donkey = Donkey()

@donkey.tool(approval="required", risk="financial")
async def issue_refund(ticket_id: str, amount: float) -> str:
    """Refund a support ticket."""
    ...
```

Calling `issue_refund` does not run it. DDK records a pending approval, emits
it on a span, and raises `ApprovalRequired`. Your code gets a decision from a
human, wherever it already asks for one, then resumes the call:

```python
from donkey_kit import ApprovalRequired, Donkey

donkey = Donkey()

async def handle(ticket_id: str, amount: float) -> None:
    try:
        await issue_refund(ticket_id, amount)
    except ApprovalRequired as err:
        # Show err.tool, err.arguments and err.risk to a reviewer: a Slack
        # message, a ServiceNow ticket, your own UI.
        ask_a_reviewer(err.approval)

async def on_decision(approval_id: str, reviewer: str, approved: bool) -> None:
    if approved:
        await donkey.approvals.resolve(approval_id, approved_by=reviewer)
    else:
        donkey.approvals.reject(approval_id, rejected_by=reviewer)
```

  **The approval is advisory.** It runs in your process, before the call. The
  gateway does not know that an approval was needed or who gave it, and
  nothing stops code that skips the decorator. Treat the approval as an audit
  record of your own process, not as an enforcement point. An approval the
  gateway enforces is the gateway's own step-up, set in its policy
  configuration and separate from this one: see
  [Gateway step-up](https://docs.donkey-kit.dev/hitl/gateway-step-up.md#gateway-step-up).

## Declaring an approval

`@donkey.tool` takes three optional keywords. Bare `@donkey.tool` is unchanged:
it marks the tool and returns the same function (see
[CLI & decorators](https://docs.donkey-kit.dev/decorators.md#donkeytool)).

| Keyword | Default | Meaning |
|---|---|---|
| `approval` | `None` | `"required"` gates the tool behind a human decision. |
| `risk` | `None` | Advisory metadata you choose, such as `"financial"`. DDK carries it on the approval and approval span events, and attaches no behaviour to it. |
| `approval_timeout` | `"15m"` | How long a pending approval waits for a decision: seconds, a `timedelta`, or a string such as `"30s"`, `"15m"` or `"1h"`. |

A gated tool keeps its name, docstring, signature and `async`-ness, so a
framework that introspects tools sees the original. `registered_tools()`
records `approval` and `risk` on its `ToolSpec`, and the `ToolSpec`'s `func` is
the gated function.

`risk=` is a label, not an enforcement level. It does not require approval by
itself: `@donkey.tool(risk="financial")` still runs normally unless you also
set `approval="required"`. Your reviewer can use the label as context, and
`donkey scan` and Exchange publication can show the declared value as metadata.
DDK does not send it with gateway tool calls, select a gateway policy from it,
or use it to request [gateway step-up](https://docs.donkey-kit.dev/hitl/gateway-step-up.md#gateway-step-up).

The call's arguments are checked against the signature before anything is
recorded. A call that could never run raises `TypeError` straight away instead
of waiting for a reviewer.

## What `ApprovalRequired` carries

`ApprovalRequired` is a `DonkeyError`, but not a `PolicyViolation`. No gateway
refused anything, and the [refusal handlers](https://docs.donkey-kit.dev/refusal-handlers.md) never see it.
`err.approval` is the pending call, an `Approval`:

| Field | What it holds |
|---|---|
| `id` | The id you pass to `resolve` or `reject`. |
| `tool` | The tool's name. |
| `arguments` | A detached snapshot of the call's arguments by parameter name, defaults included, with a read-only top-level mapping. |
| `risk` | The `risk=` the tool declared, or `None`. |
| `correlation_id` | The enclosing `donkey.run()` id, or a fresh one outside a run. |
| `requested_at`, `expires_at` | Unix timestamps. `expired` is true once `expires_at` has passed. |

`err.tool`, `err.arguments`, `err.risk` and `err.correlation_id` are shortcuts
to the same values. The exception's message names the tool and the approval id,
never the arguments.

The gate snapshots execution arguments separately. Changing the original values
or nested containers in `Approval.arguments` cannot change the pending call.
Reviewed tool arguments must be acyclic exact built-in data: `dict`, `list`, `tuple`, `set`,
`frozenset`, `bytearray`, or scalar `None`, `bool`, `int`, `float`, `complex`,
`str`, `bytes` and `range`. Custom objects (including subclasses of these types)
and cycles raise `ConfigError`; convert them to built-in data before requesting
a fresh approval. Snapshots allow at most 64 nested containers along any path,
including the outer parameter mapping. Exceeding this limit raises `ConfigError`
with remediation to reduce nesting before requesting a fresh approval.
The ADK approval plugin's trusted injected `ToolContext` is
execution-only, excluded from review and snapshots, and must be the exact
object the plugin supplied. This exception does not admit caller-supplied custom
data. Native resumes compare values and exact types recursively: `True`, `1`
and `1.0`, for example, are not interchangeable and raise `ConfigError` if changed.
Matching also preserves scalar representations: a `range` must retain its
`start`, `stop` and `step`, even if a different range yields the same sequence.
Floats and both complex components must retain their binary representations,
including signed zero. NaNs match only when their binary representations are
identical, not merely because both values are NaN.
Python tool defaults are snapshotted for execution even when a callable exposes
`__signature__`; that attribute alone does not select MCP argument behavior.

## Resolving

`donkey.approvals` holds this client's pending approvals. Each `Donkey` owns
its own, so a client resolves only the approvals its own calls recorded. A
gated call records its approval on the first of these that applies:

1. The client whose `donkey.run()` or `donkey.as_user()` block runs the call.
2. The client the tool was declared on: `@donkey.tool(approval="required")` on
   an instance, or the client whose `tools.discover()` built the
   [`ToolSet`](https://docs.donkey-kit.dev/hitl/gateway-step-up.md#discovered-tools-local-round-trip).
3. Neither: the call raises `ConfigError` and the tool does not run. This is a
   tool declared on the class, `@Donkey.tool(approval="required")`, called
   outside every block. Call it inside `donkey.run()`, or declare it on your
   `Donkey` instance.

So the decorator form at the top of this page records on `donkey`, wherever the
tool is called, unless another client's block runs it. Bare `@Donkey.tool`
without `approval=` names no client and needs none.

- `resolve(id, approved_by=...)` records the approver and runs the tool with
  the arguments it was called with. It returns what the tool returns. For an
  `async` tool that is an awaitable, so `await` it.
- `reject(id, rejected_by=...)` records who refused and drops the call without
  running it. It returns the `Approval`, so you can tell the agent.
- `pending()` lists the approvals still waiting, oldest first. `get(id)`
  returns one.

`approved_by` and `rejected_by` must be non-empty. Each approval is decided
once: resolving or rejecting an id that is no longer pending, or that another
`Donkey` recorded, raises `ApprovalNotFound`. It is a `DonkeyError` and still a
`KeyError`, so an `except KeyError` keeps working. DDK records the name you
pass and does not authenticate it.

The resumed call runs under the approval's `correlation_id`, not the
correlation of the code that resolves it. So the gateway's records for every
model call the tool makes join to the approval, however much later the
decision arrives.

### An approval never overrides a refusal

The resumed tool goes through the governed transport like any other code. The
gateway judges every model call it makes afresh, and a refusal is raised as
usual: typed inside `donkey.run()` or `@donkey.governed`, and seen by your
refusal handlers. An approval says a human allowed the tool to run. It does
not say the gateway has to accept what the tool sends.

## Who a resumed call runs as

An approved tool runs as the user who asked for it, not as the code that
resumes it. When the call pauses, DDK records the user bound by
[`donkey.as_user()`](https://docs.donkey-kit.dev/identity.md), or that none was, and the attribution bound by
`donkey.run(...)` or `donkey.attribution(...)`. When the call resumes, DDK binds
exactly those for the tool. Its model calls carry the requester's user token and
cost tags, so the gateway applies the requester's per-user policy. A reviewer's
request handler that resolves the approval lends the tool neither the
reviewer's identity nor the service's.

| The code that resumes the call | What happens |
|---|---|
| Runs outside any `as_user()` block | The tool runs as its requester. |
| Runs inside `as_user()` with the requester's token | The tool runs as its requester. |
| Runs inside `as_user()` with another token, or with any token when the requester had none | `AuthError`, and the tool is not run. A `resolve()` refused this way leaves the approval pending. |

"The same user" means the same token. DDK reads no claims, so a refreshed token
for the same person counts as another user: resume outside `as_user()`
instead. The tool sends the token recorded at the pause, and if it has expired
by then, the gateway refuses it as a typed error.

The four framework pauses below record the requester when DDK sees the pause:
the LangGraph interrupt, the Strands `ApprovalHook`, the ADK plugin, and the
initial `needs_approval` callback installed by DDK's OpenAI Agents SDK
`function_tool`. Run the agent inside the requester's `as_user()` scope so that
initial callback records the origin. `pending_approvals()` only inspects pauses:
it never reconstructs lost requester evidence or rebinds it to the inspector.
Repeated inspection in the same process preserves retained origin evidence
and emits no additional pending event.

  A framework pause keeps its requester in process memory only, for the most
  recent 1,024 pauses, by the framework's approval id. DDK never writes the
  token into a checkpoint or a run state. A pause resumed after a restart, or
  in another process, or after its requester state was evicted, raises
  `AuthError` and the tool is not run. Request a fresh approval in the original
  requester's context and resume in the same process while that state is retained.
  An `as_user()` block alone cannot restore lost requester evidence.
  Only the gated tool is re-bound, so the
  framework's other model calls in the resumed run use the resuming code's
  binding too. To run the whole resumed run as the requester, resume it inside
  their `as_user()` block.

## Timeouts

A pending approval waits `approval_timeout` (15 minutes unless the tool sets
one). After that, `resolve` and `reject` raise `ApprovalTimedOut`, and the call
is not made. `pending()` drops expired approvals. Nothing in DDK waits on a
human, so an unanswered approval never hangs your agent. Call the tool again
to ask for a fresh decision.

A pause that [LangGraph holds](https://docs.donkey-kit.dev/hitl/frameworks.md#in-a-langgraph-graph) has no timeout: it waits
in the checkpointer until the graph is resumed, and `approval_timeout` does not
apply.

## The audit trail

Each step of an approval is a short `donkey.tool.approval` span, a child of
whatever span is current, with one event:

| Event | Extra attribute |
|---|---|
| `donkey.approval.pending` | `donkey.approval.expires_at`, unless LangGraph holds the pause |
| `donkey.approval.approved` | `donkey.approval.approved_by` |
| `donkey.approval.rejected` | `donkey.approval.rejected_by` |
| `donkey.approval.timed_out` | — |
| `donkey.approval.resume_refused` | — |

`donkey.approval.resume_refused` marks a framework pause that was approved but
whose resume DDK refused with `AuthError`, because it ran as another user or
its requester state was missing (see
[Who a resumed call runs as](#who-a-resumed-call-runs-as)). The trail then
shows that the approved tool never ran.

Every event also carries `gen_ai.tool.name`, `donkey.approval.id`,
`donkey.correlation_id` and, when the tool declared one,
`donkey.approval.risk`. So "who approved this refund?" is a query on the
correlation ID. Each decision and timeout is also logged at `INFO` on the
`donkey_kit.core.approvals` logger.

The tool's arguments never go on a span or into a log record. They are request
content, the same content that [stays off spans](https://docs.donkey-kit.dev/telemetry.md#message-content-stays-off-spans-by-default)
for model calls. Show them to the reviewer from `err.arguments`.

Approval spans go to the same tracer as DDK's other spans (see
[Telemetry & cost](https://docs.donkey-kit.dev/telemetry.md)). A decorator has no `Donkey` configuration to
read, so `DONKEY_TELEMETRY=false` does not stop them reaching a
`TracerProvider` your process installed itself.

## Pending approvals live in memory

Each client's registry is in-process memory. A restart forgets every pending
approval, and resolving one after a restart raises `ApprovalNotFound`. A
framework's durable pause, such as LangGraph's checkpointer or the OpenAI Agents
SDK's serialised `RunState`, can retain the pause but not DDK's requester
evidence. After a restart, request a fresh approval before running the tool.

## Mapped onto what your framework already has

DDK does not introduce a new pause mechanism. The raise-and-resume form above
works in any Python code, and LangGraph's own `interrupt()`, the OpenAI Agents
SDK's `needs_approval`, Strands' interrupt hooks and Google ADK's tool
confirmation are mapped. The gateway's step-up is typed and waited on, not
mapped onto a pause:

| Framework / protocol | Native primitive | Status |
|---|---|---|
| Any Python code | `ApprovalRequired` + `donkey.approvals.resolve(...)` | Live |
| LangGraph | `interrupt()` / `Command(resume=…)` | Offline-verified |
| OpenAI Agents SDK | `needs_approval` / `RunState.approve(…)` | Offline-verified |
| Google ADK | tool confirmation, via `approval_plugin()` | Offline-verified |
| Strands | `BeforeToolCallEvent` interrupt, via `ApprovalHook` | Offline-verified |
| MCP | elicitation | Not supported |
| Omni Gateway | OAuth2 OBO CIBA step-up, typed as `GatewayApprovalPending` / `GatewayApprovalDenied` | Live |
| Omni Gateway, MCP `tools/list` | `ToolSet.wait_for_gateway_approval()` | Offline-verified |
| Omni Gateway, MCP `tools/call` | none: the framework's own error | Not supported |

The badges use the terms of the
[verification ledger](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md):

| Status | Means |
|---|---|
| Live | The gateway contract the row rests on was observed from a real gateway (`VERIFIED (LIVE)`). The approval in the first row runs in your process and has no gateway contract of its own; the model calls a resumed tool makes go through the live-verified proxy data plane. |
| Local round-trip | Run end to end against a real local server, not through a gateway (`VERIFIED (local round-trip)`). |
| Offline-verified | The pause and the resume pass offline against the installed framework, with the model calls answered by a mocked gateway. No run through a live gateway yet: the ledger reads `UNVERIFIED (signature confirmed; pending maintainer --live + sign-off)`. For `wait_for_gateway_approval()`, the wire shapes are live, but the SDK's listing has only been tested against a replay of them. |
| Not supported | DDK does not map onto it. |

MCP elicitation is not a client-side approval, so DDK does not map onto it. A
server sends `elicitation/create` to ask the user something while it handles a
request. The direction is server to client, so a client cannot use it to hold a
call it is about to send, and there is nothing for it to pause or resume.
Governed MCP tools get their approval from
[`require_approval()`](https://docs.donkey-kit.dev/hitl/gateway-step-up.md#discovered-tools-local-round-trip) on the `ToolSet` instead, which gates
the call before it leaves the client.

## What DDK does not build

  DDK will not ship an approval queue or an approval UI. Both are on the
  [will-not-build list](https://docs.donkey-kit.dev/roadmap.md). It integrates with whatever you already
  run: Slack, ServiceNow, or LangGraph's own checkpointer.

Specifically, DDK does not:

- store approvals durably, or share them between processes;
- notify a reviewer, escalate an unanswered approval, or retry one that timed
  out;
- decide who may approve, or authenticate the `approved_by` you pass;
- tell the gateway that a call was approved;
- decide when the gateway asks for step-up, or answer it for the user.

A queue would mean owning a durable store, an escalation model and a
notification system: a product rather than a feature.

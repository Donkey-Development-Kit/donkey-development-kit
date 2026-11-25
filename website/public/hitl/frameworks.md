# In your framework

Status: Live. Shipped, and verified against a real governed gateway.

## The framework approval helpers

LangGraph, the OpenAI Agents SDK, Strands and Google ADK each pause a gated
tool in their own way, as the sections below describe. Each adapter module has
the same three helpers for its pause, and they all speak `Approval`:

| Helper | Returns |
|---|---|
| `pending_approvals(...)` | A tuple of `Approval`, one for each paused call, in order. `native` is the framework's own pause object. |
| `approve(..., by=...)` | The `Approval`, with `native` set to the object you resume the framework on. |
| `reject(..., by=...)` | The same as `approve`. |

`Approval.native` is a frozen field that defaults to `None`. It is left out of
equality and `repr`, so an `Approval` compares and prints the same whatever
object it carries. For each framework, `native` holds:

| Framework | On a pending approval | On a decided approval |
|---|---|---|
| LangGraph | The `Interrupt` | The `Command` to `graph.invoke(...)` |
| OpenAI Agents SDK | The `ToolApprovalItem` | The `RunState` to `Runner.run(agent, ...)` |
| Strands | The `Interrupt` | The `interruptResponse` dict for `agent.invoke_async([...])` |
| Google ADK | The `adk_request_confirmation` `FunctionCall` | The `types.Part` for the resuming user message |

The helpers all return `Approval`, never a framework object, so a reviewer
queue is written once. It reads the same fields for every framework, and only
the step that resumes the agent touches `native`. An approval from
`donkey.approvals` (the raise-and-resume form above) has `native=None`.

## In a LangGraph graph

Inside a LangGraph graph that runs with a checkpointer and a `thread_id`, a
gated tool does not raise `ApprovalRequired`. It pauses with LangGraph's own
`interrupt()`, from a plain node or from a `ToolNode`, and you resume it with
`Command(resume=...)`. Nothing changes on the tool:

```python
from donkey_kit import Donkey
from donkey_kit.integrations.langgraph import approve, pending_approvals, reject
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

donkey = Donkey()

@donkey.tool(approval="required", risk="financial")
def issue_refund(ticket_id: str, amount: float) -> str:
    """Refund a support ticket."""
    return f"refunded {ticket_id}"

def refund(state: dict) -> dict:
    return {"result": issue_refund(state["ticket_id"], state["amount"])}

builder = StateGraph(dict)
builder.add_node("refund", refund)
builder.add_edge(START, "refund")
builder.add_edge("refund", END)
graph = builder.compile(checkpointer=MemorySaver())
thread = {"configurable": {"thread_id": "ticket-T-1"}}

paused = graph.invoke({"ticket_id": "T-1", "amount": 140.0}, thread)
(approval,) = pending_approvals(paused)  # show this to a reviewer

done = graph.invoke(approve(approval, by="m.keller").native, thread)
```

`pending_approvals` takes what `graph.invoke()` returned on the pause, or
`graph.get_state(thread)`. It returns an `Approval` for each DDK interrupt and
leaves your own interrupts out. It records nothing, because the pending
approval was recorded when the graph paused.

`approve(approval, by=...)` returns the `Approval` with `native` set to the
`Command` that resumes the graph. Its resume value is keyed by the interrupt's
id, so when several gated calls pause at once, merge their `native.resume`
mappings into one `Command(resume={...})`. `reject(approval, by=...)` builds a
refusing `Command` the same way. There is no `message`, because the refused
tool raises `ApprovalRejected` (see below). Both raise `ValueError` for an
`Approval` that `pending_approvals` did not return, or for an empty `by`.

The interrupt's value describes the call. This is what `pending_approvals`
reads, and what you would read without it:

| Key | What it holds |
|---|---|
| `type` | `"donkey.approval"`, so you can tell it from your own interrupts. |
| `approval_id` | The approval's id, the same one on its span events. |
| `tool`, `arguments`, `risk` | As on an `Approval`. `arguments` is a plain `dict`. |
| `correlation_id` | The enclosing `donkey.run()` id, or a fresh one outside a run. |
| `requested_at` | When the call paused, as on an `Approval`. |

You can also resume without the helpers. Resume on
`Command(resume={"approved": True, "by": "<approver>"})` to record the approver
and run the tool, or on `{"approved": False, "by": "<approver>"}` to refuse it.
A refused call raises `ApprovalRejected` from the tool, which carries the
`Approval`. A `ToolNode(handle_tool_errors=True)` turns that error into a tool
message for the model. Any other resume value raises `ValueError`. `by` must be
a non-empty string, and DDK records it without authenticating it.

The pause lives in your checkpointer, not in `donkey.approvals`: `pending()`
does not list it, and `resolve` and `reject` do not apply to it. A durable
checkpointer keeps it across a restart, but a fresh process cannot run the gated
tool without the retained requester evidence: it raises `AuthError`. Request a
fresh approval instead. The approval id is derived from the thread, the tool
and the arguments. The
interrupt's value, arguments included, is stored by your checkpointer like the
rest of the graph's state.

A refusal and the pause compose. A model call refused before the gated tool
raises typed, and no approval is requested. A model call the approved tool
makes is judged by the gateway as usual, and its refusal leaves the graph
typed.

  **LangGraph re-runs a node from its start on resume.** If one node calls two
  gated tools, or the model asks for two in one turn, LangGraph pauses for each
  in turn, and resuming the second runs the first approved tool again. Give the
  model one gated call per turn (for example `parallel_tool_calls=False`), or
  make the tool safe to repeat.

The resumed tool runs under the correlation id of the `donkey.run()` that
resumes the graph, or the pause's own outside a run. To join the gateway's
records to the pause, resume under the same run id you paused under.

Two cases fall back to the raise-and-resume form above, with
`ApprovalRequired` raised out of the graph: a graph compiled without a
checkpointer, which has no thread to resume, and, on Python 3.10, a graph run
with `ainvoke()`, where LangGraph cannot see the run's config from inside the
tool (see [LangGraph](https://docs.donkey-kit.dev/frameworks/langgraph.md#interrupt-composes-with-typed-refusals)).

## In an OpenAI Agents SDK run

The OpenAI Agents SDK has its own tool approval: a `FunctionTool` with
`needs_approval` stops `Runner.run` before the tool runs and returns the call in
`result.interruptions`. Build the agent's tool with
`donkey_kit.integrations.openai_agents.function_tool`, and a gated
`@donkey.tool` sets it for you:

```python
from agents import Agent, Runner
from donkey_kit import Donkey
from donkey_kit.integrations import openai_agents

donkey = Donkey()

@donkey.tool(approval="required", risk="financial")
def issue_refund(ticket_id: str, amount: float) -> str:
    """Refund a support ticket."""
    return f"refunded {ticket_id}"

agent = Agent(
    name="refunds",
    model=donkey.openai_agents.model("gpt-4o"),
    tools=[openai_agents.function_tool(issue_refund)],
)

async with donkey.run(id="ticket-T-1"):
    result = await Runner.run(agent, "Refund ticket T-1")
    (approval,) = openai_agents.pending_approvals(result)  # show this to a reviewer

state = result.to_state()
async with donkey.run(id=approval.correlation_id):
    decided = openai_agents.approve(state, approval, by="m.keller")
    done = await Runner.run(agent, decided.native)  # decided.native is state
```

`pending_approvals(result)` returns an `Approval` for each paused call to a
gated tool, with the SDK's `ToolApprovalItem` on `native`. The initial
`needs_approval` callback records the requester and pending span event;
inspection records neither, even when inspecting restored state. Repeated
same-process inspection is idempotent for retained requester evidence. It skips
interruptions it did not gate, so your own
`needs_approval` tools are left to you. `approve(state, approval, by=...)`
records the approver and approves the call on the `RunState`. It returns the
`Approval` with `native` set to that `RunState`, and the next
`Runner.run(agent, state)` runs the tool once.
`approve` and `reject` also take the approval's id, and raise `KeyError` for an
id with no paused call in the state. A call is decided once: a second `approve`
or `reject` of it on the same `RunState` raises `KeyError` and records nothing.
DDK remembers the decisions on each `RunState` object, so decide on the state you
resume, because each `result.to_state()` builds a new one. Passed an `Approval`,
the decision keeps its correlation id, so it joins the pending event even when
you decide outside the `donkey.run()` that paused. `by` must be a non-empty
string, and DDK records it without authenticating it.

`reject(state, approval, by=..., message=None)` records the rejecter, rejects
the call and returns the `Approval` as `approve` does. The Agents SDK helpers
take the state as well as the approval because the SDK records the decision on
the `RunState`. The tool does not run, and the run goes on: the Agents SDK
hands the model `message`, or a default that names the tool, as the tool's
result. It does not raise `ApprovalRejected`, and the rejecter's name never
reaches the model.

The pause lives in the `RunState`, not in `donkey.approvals`. It survives
`state.to_string()` and `RunState.from_string(agent, ...)`, but that serialization
does not retain DDK's in-process requester evidence. After losing that evidence,
request a fresh approval; a native resume raises `AuthError` without running
the tool. The approval id
joins the pending and approved events because DDK derives it from the call id,
the tool and canonical JSON arguments, independent of JSON key order or
whitespace. This stable id does not recreate requester evidence. Decide through `approve` and `reject`: calling
`RunState.approve` yourself runs the tool with no approver on record, and DDK
does not offer the SDK's `always_approve` or `always_reject`, which would let
later calls run with no decision recorded.

The resumed tool runs under the correlation id of the `donkey.run()` that
resumes the run, or the approval's own outside a run. A model call the approved
tool makes is judged by the gateway as usual, and its refusal leaves
`Runner.run` typed instead of becoming a tool result.

The same approval helpers work with `agents.tool_namespace(...)` and
`function_tool(..., defer_loading=True)` on a Responses model you construct
yourself. Calls are matched to the tool's namespace and deferred identity, so
tools with the same routed name in different namespaces keep their own risk
labels, argument defaults and decisions. `Approval.tool` still names the
underlying `@donkey.tool`; decide using the approval object or its id, not a
bare routed name. These paths are tested offline at `openai-agents` 0.20.0 and
0.24.0, not live against a gateway. DDK's `model()` returns a Chat Completions
model, which the Agents SDK does not allow to use namespaced or deferred tools.

Wrap a gated tool with the DDK `function_tool`, not the SDK's own
`agents.function_tool`. The SDK's own wrapper does not pause: the gate raises
`ApprovalRequired`, and the SDK hands it to the model as a tool-result string.
The DDK `function_tool` refuses a `needs_approval` keyword for a gated tool, and
if the SDK's approval is switched off on the built tool, `ApprovalRequired`
leaves `donkey.run()` typed and the tool does not run.

## In a Strands agent

A Strands agent pauses a gated tool with Strands' own interrupt once you give
it an `ApprovalHook`. The hook raises the interrupt from `BeforeToolCallEvent`,
before the tool runs, and the agent stops with `stop_reason == "interrupt"`.
Wrap the gated function in Strands' `tool()` as you would any other:

```python
from donkey_kit import Donkey
from donkey_kit.integrations.strands import ApprovalHook, approve, pending_approvals, reject
from strands import Agent, tool

donkey = Donkey()

@donkey.tool(approval="required", risk="financial")
def issue_refund(ticket_id: str, amount: float) -> str:
    """Refund a support ticket."""
    return f"refunded {ticket_id}"

agent = Agent(
    model=donkey.strands.model("gpt-4o"),
    tools=[tool(issue_refund)],
    hooks=[ApprovalHook()],
)

result = await agent.invoke_async("Refund ticket T-1, 140 euros")
if result.stop_reason == "interrupt":
    approvals = pending_approvals(result)  # show these to a reviewer
    decided = [approve(a, by="m.keller") for a in approvals]
    result = await agent.invoke_async([a.native for a in decided])
```

`pending_approvals(result)` returns an `Approval` for each paused call, with
Strands' own `Interrupt`, named `"donkey.approval"`, on `native`. The
interrupt's `reason` describes the call:

| Key | What it holds |
|---|---|
| `approval_id` | The approval's id, the same one on its span events. |
| `tool`, `arguments`, `risk` | As on an `Approval`. `arguments` is a plain `dict`. |
| `correlation_id` | The enclosing `donkey.run()` id, or a fresh one outside a run. |
| `requested_at` | When the call paused, as on an `Approval`. |

Resume the agent on a list of responses, one for each paused call. Each helper
returns the `Approval` with the Strands `interruptResponse` on `native`:

- `approve(approval, by=...)` records the approver and runs the tool once,
  under the approval's correlation id, while requester evidence is retained
  and the resumed arguments match.
- `reject(approval, by=..., message=...)` records who refused, and the tool is
  not run. Strands sends the model `message` as an error tool result, or a
  default that names the tool, never the rejecter. Nothing raises `ApprovalRejected`:
  the model reads the rejection and carries on.

Both raise `ValueError` for an `Approval` that `pending_approvals` did not
return. `by` must be a non-empty string, and DDK records it without authenticating it.
Each paused call is decided once, even if another hook retries the tool. When
the model asks for several gated calls in one turn, Strands' default concurrent
executor pauses them together, and a `SequentialToolExecutor` pauses on each in
turn.

Strands holds the pause, not `donkey.approvals`: `pending()` does not list it,
and `approval_timeout` does not apply. The approval id is derived from Strands'
tool-use id, the tool and the arguments. Restoring the agent from a Strands
session and adding a new `ApprovalHook` does not restore DDK's requester
evidence. A native resume in a fresh process raises `AuthError` without running
the tool; request a fresh approval instead.

A model call the approved tool makes is judged by the gateway as usual.
`ApprovalHook` is a `GovernedToolHook`: it re-raises a governed refusal from
inside a tool before Strands can retry the tool or send the refusal text to the
model. Wrap the invocation in `donkey.run()` or `@donkey.governed` to receive
the typed error rather than Strands' event-loop exception. This also applies to
ungated tools on the same agent. An agent with no gated tools gets the same
from `GovernedToolHook()` alone (see [Strands](https://docs.donkey-kit.dev/frameworks/strands.md#notes)).
Ordinary tool failures remain error tool results for the model, as do human
rejections.

Without the hook, nothing pauses. The gate raises `ApprovalRequired` inside the
tool, Strands turns it into an error tool result, and the call waits in
`donkey.approvals` like any other.

## In a Google ADK app

An ADK app pauses a gated tool with ADK's own tool confirmation once you add
the plugin `approval_plugin()` builds. Before the tool runs, the plugin asks
ADK for a confirmation, and the run ends its turn with an
`adk_request_confirmation` call. List the gated function in the agent's
`tools` as you would any other:

```python
from donkey_kit import Donkey
from donkey_kit.integrations.adk import approval_plugin, approve, pending_approvals, reject
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

donkey = Donkey()

@donkey.tool(approval="required", risk="financial")
def issue_refund(ticket_id: str, amount: float) -> str:
    """Refund a support ticket."""
    return f"refunded {ticket_id}"

agent = LlmAgent(name="refunds", model=donkey.adk.model("gpt-4o"), tools=[issue_refund])
app = App(name="refunds", root_agent=agent, plugins=[approval_plugin()])
sessions = InMemorySessionService()
runner = Runner(app=app, session_service=sessions)
session = await sessions.create_session(app_name="refunds", user_id="u")

async def send(message: types.Content) -> list:
    run = runner.run_async(user_id="u", session_id=session.id, new_message=message)
    return [event async for event in run]

events = await send(types.Content(role="user", parts=[types.Part(text="Refund T-1, 140 euros")]))
approvals = pending_approvals(events)  # show these to a reviewer
if approvals:
    decided = [approve(a, by="m.keller") for a in approvals]
    events = await send(types.Content(role="user", parts=[a.native for a in decided]))
```

`pending_approvals(events)` returns an `Approval` for each unanswered request
among the events you pass it. Pass the latest run's events, or the session's
`events`. The runner does not yield the user message that answers a request,
so the events of several runs put together still list a request you already
answered. Each approval's `native` is ADK's own `adk_request_confirmation`
function call. The call's `args["toolConfirmation"]["payload"]` describes the
tool call:

| Key | What it holds |
|---|---|
| `kind` | `"donkey.approval"`, so you can tell a DDK approval from other confirmations. |
| `approval_id` | The approval's id, the same one on its span events. |
| `tool`, `arguments`, `risk` | As on an `Approval`. `arguments` holds the arguments as the model sent them. |
| `correlation_id` | The enclosing `donkey.run()` id, or a fresh one outside a run. |
| `requested_at` | When the call paused, as on an `Approval`. |

Resume the run with one user message whose parts answer the requests. Each
helper returns the `Approval` with that part, a `types.Part`, on `native`:

- `approve(approval, by=...)` records the approver and runs the tool under the
  pause's correlation id, in this process or another.
- `reject(approval, by=..., message=...)` records who refused, and the tool is
  not run. The model gets `message` as the tool's error result, or a default
  that names the tool, never the rejecter. Nothing raises `ApprovalRejected`:
  the model reads the rejection and carries on.

Both raise `ValueError` for an `Approval` that `pending_approvals` did not
return. `by` must be a non-empty string, and DDK records it without
authenticating it. ADK keeps the confirmation calls and their answers out of
the model's context, so the approver's name never reaches the model.

The plugin checks each answer against the request the agent stored in the
session, not against what the answer carries. It refuses, without running the
tool, an answer to a request the agent never made, one that is not `approve()`
or `reject()`'s (such as a plain ADK confirmation), and a second answer for a
call already answered; the model gets the refusal as the tool's error result.
So the tool runs once per approval, even on `google-adk` 2.4.0, which would
otherwise resume on a confirmation a user message forges and run a call again
when the same answer is sent twice.

On `google-adk` releases before 2.13, the pause's last event also carries
each paused call's placeholder result, `{"status": "awaiting approval",
"approval_id": ...}`, as text. Don't show it as the agent's answer.

ADK holds the pause, not `donkey.approvals`: `pending()` does not list it, and
`approval_timeout` does not apply. A persistent `SessionService` retains the
pause's events, but a fresh process and plugin do not retain DDK's requester
evidence. A native resume then raises `AuthError` without running the tool;
request a fresh approval instead. The approval id is derived from ADK's function-call id, the
tool and the arguments as sent, so the decision is recorded under the same id
and joins the pause's correlation id.

A model call the approved tool makes is judged by the gateway as usual. An
exception the tool raises, a refusal included, leaves the runner as any tool
exception does in ADK.

Without the plugin, nothing pauses. The gate raises `ApprovalRequired` inside
the tool, which leaves the runner, and the call waits in `donkey.approvals`
like any other.

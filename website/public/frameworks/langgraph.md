# LangGraph

LangGraph (and LangChain more broadly) gets a governed `ChatOpenAI` pointed at
your Agent Fabric LLM proxy. LangGraph is the **deep adapter**: every proxy
header and the SDK's shared async transport reach the native client, and the
adapter runs the full conformance suite in CI.

**What you get**

- A native `langchain_openai.ChatOpenAI` — nothing LangGraph-specific wraps it.
- Per-run correlation IDs that reach every graph node.
- Typed gateway refusals (`PIIDetected`, `TokenBudgetExceeded`, …) inside nodes.
- A conformance suite you can run against your own graph.

## Install

```bash
pip install "donkey-kit[langgraph]"
```

## Quickstart

```python
from donkey_kit.integrations.langgraph import chat_model

llm = chat_model("gpt-4o")
```

`llm` is a real `langchain_openai.ChatOpenAI` instance. Drop it straight into
your graph nodes or chains.

Call the proxy's OpenAI-compatible API with the official `openai` npm client.
The same base URL and `client_id`/`client_secret` headers also work with
**LangChain.js** (`ChatOpenAI`, via `configuration.baseURL` +
`defaultHeaders`).

```typescript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: process.env.DONKEY_LLM_PROXY_URL,   // no /v1
  apiKey: "unused",                              // required slot; proxy uses the headers below
  defaultHeaders: {
    client_id: process.env.DONKEY_LLM_PROXY_CLIENT_ID!,
    client_secret: process.env.DONKEY_LLM_PROXY_CLIENT_SECRET!,
  },
});

const reply = await client.chat.completions.create({
  model: "gpt-4o",
  messages: [{ role: "user", content: "Say hi in three words." }],
});
console.log(reply.choices[0].message.content);
```

## Three ways to construct

**1. Off a shared `Donkey` instance** (reuses one HTTP client and lifecycle
across every adapter you use in a run):

```python
from donkey_kit import Donkey

async with Donkey.from_env() as donkey:
    llm = donkey.langgraph.chat_model("gpt-4o")
```

The adapter is also callable: `donkey.langgraph("gpt-4o")` is the same as
`donkey.langgraph.chat_model("gpt-4o")`.

**2. Module-level factory** (shortest — uses a cached, env-configured default
`Donkey`):

```python
from donkey_kit.integrations.langgraph import chat_model

llm = chat_model("gpt-4o")
```

**3. Governed kwargs, native constructor** (you call `ChatOpenAI` yourself):

```python
from donkey_kit import Donkey
from langchain_openai import ChatOpenAI

async with Donkey.from_env() as donkey:
    llm = ChatOpenAI(model="gpt-4o", **donkey.langgraph.connection_kwargs())
```

## Manual equivalent

The factories make this native constructor call for you:

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="gpt-4o",
    base_url=...,             # from DONKEY_LLM_PROXY_URL, no /v1 suffix
    api_key=...,
    default_headers=...,      # client_id / client_secret header pair, not bearer
    http_async_client=...,    # the SDK's shared httpx async client (ainvoke)
    http_client=...,          # the SDK's shared blocking client (invoke)
    max_retries=0,            # the SDK retries in its own transport layer
    use_responses_api=True,   # /responses, the route the raw client and the simulator use
)
```

`connection_kwargs()` returns exactly these keys, so you can drop the factory
and construct `ChatOpenAI` by hand at any time. An OpenAI-format proxy serves
both the Responses API (`/responses`) and `/chat/completions`. The adapter uses
`/responses` because the raw `donkey.llm` client does, and because the local
simulator, which the conformance suite runs against, serves only that route.
Pass `use_responses_api=False` to `chat_model(...)` to call
`/chat/completions` instead. A `base_url` / `openai_api_base` passed to
`chat_model(...)` must pass the
[`https://` rule](https://docs.donkey-kit.dev/reference/configuration.md#endpoints-must-use-https).

## Graph-level features

### Correlation IDs reach every node

Bind a run ID once with `donkey.run(id=…)` and every node sees it via
`current_correlation_id()`, with nothing threaded through graph state.
LangGraph runs nodes on `asyncio` tasks that copy the current context, so the
ID propagates on its own:

```python
from donkey_kit.core.telemetry import current_correlation_id

async def prepare(state):
    logger.info("handling", extra={"correlation_id": current_correlation_id()})
    return {}

async with donkey.run(id=ticket.id):
    await graph.ainvoke({"messages": [("user", ticket.text)]})
```

### Typed refusals inside a node

On a proxy refusal, LangChain raises its own wrapped
`OpenAIPermissionDeniedError`, not the SDK's typed exception. Wrap the model
call in `typed_refusals()` and a gateway refusal comes back through the
[error taxonomy](https://docs.donkey-kit.dev/errors.md) instead:

```python
from donkey_kit.integrations.langgraph import typed_refusals

async def call_model(state):
    with donkey.langgraph.typed_refusals():   # or: with typed_refusals():
        reply = await model.ainvoke(state["messages"])
    return {"messages": [reply]}
```

A PII block now propagates out of `graph.ainvoke(...)` as `PIIDetected`, a
budget block as `TokenBudgetExceeded`, and so on — each carrying the
correlation and call IDs the client sent. Transport-level errors with no HTTP
response (connection failures, timeouts) pass through unchanged.

The typed error is raised without a chained cause, because LangChain's error
message repeats the gateway's rejection text, which for a PII block includes
the flagged values, and a traceback prints every chained exception. The
original LangChain error is on `err.framework_error`; `err.__cause__` is
`None`. No frame in the traceback holds it as a local variable, so reporters
that print frame locals (Sentry, `pytest -l`) don't show it.

### `interrupt()` composes with typed refusals

A human-in-the-loop `interrupt()` and a typed refusal don't interfere: the
graph pauses cleanly at the interrupt, and on resume a refusal in a downstream
model node still surfaces as its typed exception.

On Python 3.10, `interrupt()` only works when the graph runs with
`graph.invoke()`. Under `graph.ainvoke()` it raises `RuntimeError: Called
get_config outside of a runnable context`, whether the node is async or sync.
LangGraph reads the run's config from a context variable that asyncio tasks
can't carry before Python 3.11, and `interrupt()` takes no config argument to
pass it in. Use Python 3.11 or later for async graphs that interrupt.

### Run the conformance suite against your own graph

The suite that tests this adapter is also a pytest plugin you can point at
your own agent:

```bash
pytest --donkey-conformance --donkey-agent=my_app:build
```

`build` returns an object with an awaitable `run(text)`. The suite checks that
it doesn't retry a budget refusal, surfaces `PIIDetected` typed, carries the
correlation ID into its logs, and tolerates a response with no budget headers.
The [`examples/langgraph`](https://github.com/Donkey-Development-Kit/donkey-development-kit/tree/main/python/examples/langgraph)
factory has exactly this shape. See [Testing](https://docs.donkey-kit.dev/testing.md).

### Which provider served this?

A call routed by the gateway to Gemini, Anthropic, or Bedrock still comes back
with `response_metadata["model_provider"] == "openai"` on the LangChain
message. That is not a routing bug — LangChain stamps `model_provider` from
the **client class** (`ChatOpenAI`, OpenAI-compatible), not from whichever
upstream the gateway actually picked. Don't use it for routing attribution;
use one of the following instead.

**Per context — `donkey.last_call`.** Right after `ainvoke`, read the record
the gateway reported for the most recent call:

```python
reply = await model.ainvoke(state["messages"])

r = donkey.last_call
r.served_provider  # e.g. "gemini"
r.served_model
r.routing_type
r.fallback
r.substituted
```

`last_call` is contextvar-scoped: it is the last call **in the current task**,
so it does not survive into graph state and does not leak back to a parent
that gathered parallel branches (each branch runs on its own `asyncio` task
with its own copy). Read it immediately after the call it describes.

LangChain sends an `ainvoke` request from a task of its own, so a model built by
`donkey.langgraph(...)` carries a callback that brings the record back to the
task that called `ainvoke`. A `ChatOpenAI` you build from `connection_kwargs()`
has no such callback: after its `ainvoke` (but not `invoke` or `astream`),
`last_call` stays `UNOBSERVED`. A batch (`abatch`, or `agenerate` with several
inputs) runs its requests side by side, so it leaves no record in the caller
either. See the
[full field reference](https://docs.donkey-kit.dev/reference/last-call.md#routing--fallback) for every
field.

**Per message — opt-in headers.** For attribution that needs to travel with
the message itself (into checkpoints, across the boundary where `last_call`
would go stale), pass `include_response_headers=True` — this is
`langchain_openai.ChatOpenAI`'s own field, forwarded through
`chat_model(**kwargs)` like any other native kwarg:

```python
model = donkey.langgraph.chat_model("gpt-4o", include_response_headers=True)
msg = await model.ainvoke(...)
headers = msg.response_metadata["headers"]
headers.get("x-llm-proxy-llm-provider")   # e.g. "gemini"
headers.get("x-llm-proxy-llm-model")
headers.get("x-llm-proxy-routing-fallback")
headers.get("x-llm-proxy-routing-type")
```

Because this is langchain-openai's own behavior, not the SDK's, its exact
shape tracks that package's version:

- It copies **all** response headers into `response_metadata["headers"]` on
  every message — and therefore into every checkpoint that stores the
  message.
- On a streamed response, the headers land on the **first chunk only**.
- Whether headers are captured on the chat-completions + `response_format`
  path has varied by langchain-openai version — verify against the version
  you have installed rather than assuming either way.

**Traces.** The OTel GenAI span for the call already carries the served
provider and model under `gen_ai.system` / `gen_ai.response.model` (see
[Telemetry](https://docs.donkey-kit.dev/telemetry.md)) — but a LangSmith-style tracer that reads LangChain's
own fields instead of the span will still show `openai`.

## Notes

- `base_url`, `api_key`, `default_headers`, `http_async_client` and
  `http_client` are all forwarded, so proxy auth headers and the SDK's
  transport (retries, correlation IDs) reach every request, from `ainvoke()`
  and `invoke()` alike, so `donkey.simulate()` and `donkey.last_call` cover
  both. In `jwt` mode only `ainvoke()` / `astream()` carry the JWT: `invoke()`
  and `stream()` raise `ConfigError` before sending anything.
- Printing the model shows `client_secret`: `ChatOpenAI`'s own `repr()` /
  `str()` include `default_headers`. Don't print or log it; see
  [What printed output hides](https://docs.donkey-kit.dev/reference/configuration.md#what-printed-output-hides).
- `max_retries=0` is intentional: retries live in the SDK's transport layer,
  so the SDK and the OpenAI client don't both retry.
- The proxy is OpenAI-compatible but not the full OpenAI API: the base URL has
  no `/v1` prefix, there is no `/models` endpoint, and auth is a
  `client_id`/`client_secret` header pair rather than a bearer token.

See the [error taxonomy](https://docs.donkey-kit.dev/errors.md) for how proxy rejections surface as typed
exceptions, and [Which provider served this?](#which-provider-served-this)
for reading the served provider/model instead of LangChain's own
`model_provider`.

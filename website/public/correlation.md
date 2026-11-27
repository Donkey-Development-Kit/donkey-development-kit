# Correlation IDs

Status: Live. Shipped, and verified against a real governed gateway.

<span id="correlation-ids" />

Set a per-**run** id once, and every call inside the block carries it — on the
wire, on every span, and on every exception — with nothing threaded through your
framework state.

```python
async with donkey.run(id=ticket.id):
    await triage_agent.run(ticket)
```

`donkey.run(id=…)` binds **two ids**:

- a **run id** → the `X-Correlation-Id` request header → the span's
  `donkey.correlation_id` → `DonkeyError.correlation_id`. Shared by every call in
  the block, so a client-side log line **joins** to the gateway's own record.
- a fresh **per-call id** → the `X-Donkey-Request-Id` request header →
  `DonkeyError.call_id`. Unique per logical request, stable across that request's
  retries, so one call is pinpointable within a run.

The gateway reads the inbound `X-Correlation-Id` and echoes it verbatim on the
response, so the request and response `x-correlation-id` are the same value.
`X-Donkey-Request-Id` is a client-owned per-call id the gateway does not consume.
Both header names are overridable (`correlation_header` / `call_id_header`) for a
gateway that expects different ones.

Propagation is contextvar-based, so it reaches through framework nodes (every
LangGraph node, for instance) without threading an argument through every
function, and concurrent runs never leak into each other. It works with or
without OpenTelemetry installed. `donkey.run(...)` is a **dual sync/async**
context manager (plain `with` works too); nested blocks rebind then restore.

**Concurrent fan-out.** Every call in a fan-out started inside `donkey.run()`
(`asyncio.gather`, parallel nodes, tool calls on child tasks) carries exactly
one correlation id. Each task copies the context when it is created, so this
holds even for a task that outlives the block, and a task that opens its own
`donkey.run()` changes only its own id, never its parent's or a sibling's.
Threads do not inherit the context: run them under `contextvars.copy_context()`
or open a `donkey.run()` inside the thread. **Outside** a run nothing is
grouped. Each call gets its own fresh id, so N concurrent calls send N ids.

A refusal leaving the block is re-raised as its typed `DonkeyError`, whichever
framework wrapped it on the way out, so `except PIIDetected` works the same
around every framework. See
[Typed refusals at the framework boundary](https://docs.donkey-kit.dev/errors.md#typed-refusals-at-the-framework-boundary);
`donkey.run(typed_refusals=False)` turns it off.

## Across agent hops

An agent that serves requests can **join** its caller's run instead of starting
a fresh one. `donkey.run_from_headers()` reads the configured correlation header
from the inbound request, adopts the caller's id when it is well-formed
(`[A-Za-z0-9._:-]`, at most 128 characters), and mints a fresh one otherwise. A
malformed id is never forwarded. Every governed call in the block then carries
it, exactly as inside `donkey.run(id=…)`.

```python nocheck
@app.post("/tasks")
async def handle(request: Request, task: Task):
    async with donkey.run_from_headers(request.headers):
        return await agent.run(task)
```

For an outbound hop the governed transport does not carry, such as another
agent's endpoint or an MCP server reached on your own HTTP client, pass
`donkey.correlation_headers()`. It returns the bound run id under the configured
header, or `{}` outside a run, and never the gateway credentials.

```python nocheck
async with donkey.run(id=ticket.id):
    await agents.post(sub_agent_url, json=task, headers=donkey.correlation_headers())
```

The sub-agent's spans and the gateway's record then share one id end to end. The
GenAI span inside `run_from_headers()` records `donkey.correlation_id.source`:
`adopted` when the caller supplied the id, `minted` when it did not. A served
[A2A task](https://docs.donkey-kit.dev/a2a/call.md) applies the same rule on its own.

## Run ledger

`donkey.last_call` keeps only the most recent call. A `donkey.run()` block also
keeps a **run ledger**: every governed model call made in the run, every refusal
the gateway returned, and the run's token totals. Read it inside the block at
`donkey.current_run`. Keep the scope to read it after the block ends:

```python
from donkey_kit import PIIDetected

scope = donkey.run(id=ticket.id)
async with scope as run_id:                 # still the run's id, a str
    await triage_agent.run(ticket)
    run = donkey.current_run
    if any(r.is_a(PIIDetected) for r in run.refusals):
        ...                                 # redact the later prompts

ledger = scope.ledger                       # still readable after the block
ledger.call_count                           # every governed model call in the run
ledger.usage.total_tokens                   # summed across them, cached and reasoning tokens too
[c.served_model for c in ledger.calls]      # each entry is that call's last_call record
```

| Field | What it holds |
|---|---|
| `calls` | Each call's [`last_call` record](https://docs.donkey-kit.dev/reference/last-call.md), oldest first: served model and provider, fallback, request id, usage, semantic-cache and semantic-routing outcome |
| `refusals` | A `RunRefusal` for each policy refusal (`PIIDetected`, `TokenBudgetExceeded`, …) the gateway returned on a call in the run, **even when a framework caught it**, and for any `ModelSubstituted` the SDK raised: its `kind`, `policy`, `status_code`, the three ids and, for PII, the entity types. Test it with `r.is_a(PIIDetected)` |
| `usage` | A `RunUsage` with the run's `input_tokens`, `output_tokens`, `total_tokens`, `cached_tokens`, `cache_write_tokens` and `reasoning_tokens`, and `calls_with_usage` |
| `call_count`, `refusal_count` | Totals for the whole run |
| `tool_calls` | Tool invocations the SDK saw: per-tool MCP bindings and approval-gated tools. Not a framework-owned whole-server MCP call or a plain `@donkey.tool` function |
| `refusal_counts` | Refusals by typed error name, e.g. `{"PIIDetected": 2}`. Counts past the cap |
| `duration_s` | Wall-clock seconds from the block's entry to its exit, or to now while it runs |
| `spend` | What the gateway reported the run spent, or `None`. The SDK never computes it |
| `dropped` | Calls and refusals the cap dropped from `calls` and `refusals` |
| `available`, `surface` | `False`, with the adapter names, when nothing used can feed the ledger |

The same totals are recorded on the run's `donkey.run` span at block exit; see
[What a run cost](https://docs.donkey-kit.dev/telemetry.md#what-a-run-cost).

Each read returns a frozen snapshot, so read again for newer state. The ledger
holds metadata only, never prompt or completion text. A refusal is recorded as a
`RunRefusal`, not the exception, because the exception's `.response` keeps the
gateway's body, which can echo the values a policy blocked.

- **Scope.** The ledger follows the run's correlation id. Framework-spawned
  tasks append to their run's ledger, concurrent runs never see each other's,
  and a nested `donkey.run()` keeps its own and restores the outer one on exit.
  Appends are thread-safe for threads run under `contextvars.copy_context()`.
- **Bounded memory.** `calls` and `refusals` each keep the newest 1000 entries
  (`donkey.run(ledger_max_entries=…)`). Older ones are counted in `dropped`. The
  totals keep counting past the cap, so a long run's cost stays exact.
- **Same reach as `last_call`.** Only responses through the SDK's shared HTTP
  client reach it. When every adapter used routes outside that client (CrewAI
  today), `available` is `False` and `surface` names them: the ledger is
  unavailable, not empty. See [When `last_call` is unavailable](https://docs.donkey-kit.dev/frameworks/routing.md#when-last_call-is-unavailable).

## The decorator form

When a whole function should be one run, `@donkey.governed` is the decorator
equivalent of wrapping its body in `donkey.run()`:

```python
@donkey.governed(team="support")
async def handle_ticket(ticket):
    await triage_agent.run(ticket)
```

Each call opens its own run — a fresh run/correlation id — and binds the
optional per-run cost tags, the OTel span, and typed refusals, exactly the scope
`donkey.run()` establishes. It wraps **both sync and async** callables and is
usable bare (`@donkey.governed`) or parametrised. There is deliberately
**no `id=`**: pinning one id across every call would collapse unrelated runs into
a single correlation, so when you need a specific id, use `donkey.run(id=…)`
directly.

## Next steps

- [Cost attribution](https://docs.donkey-kit.dev/cost-attribution.md): the per-call tags that travel with the same run.
- [OpenTelemetry spans](https://docs.donkey-kit.dev/telemetry.md): the spans that carry the correlation ID.
- [Refusal handlers](https://docs.donkey-kit.dev/refusal-handlers.md): acting on refusals inside a `donkey.run()` scope.
- [Typed refusals](https://docs.donkey-kit.dev/errors.md): the `.correlation_id` field on every exception.

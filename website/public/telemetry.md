# Telemetry & cost

  **Shipped in Phase 1.** OpenTelemetry GenAI span export (#194), correlation
  IDs (#195), cost-attribution tags (#196), and routing and resilience signals
  (#309) are available. Platform-facing verification caveats are called out
  below; see [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md) and
  [Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md).

Two pieces of the six-piece minimum land here, because they answer the same
two questions: *what happened?* and *who pays for it?*

## OpenTelemetry GenAI spans

Every governed call produces a span following the OpenTelemetry **GenAI
semantic conventions** — `gen_ai.system`, `gen_ai.request.model`,
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` — plus attributes
for the governance layer that generic instrumentation cannot know about:

```
donkey.policy.decision      = allow | refuse
donkey.policy.type          = pii_detected | token_budget | injection | …
donkey.budget.remaining     = 18450
donkey.correlation_id       = …
donkey.cost.team            = support
donkey.cost.project         = triage-v2
donkey.cost.env             = prod
donkey.cost.enduser.id      = user-42
donkey.routing.type         = ModelBased          # how the gateway routed
donkey.routing.fallback     = false               # did it fail over?
gen_ai.response.model       = gpt-5.1             # the model that actually served
donkey.usage.cached_tokens        = 512   # omitted when the provider reports none
donkey.usage.cache_write_tokens   = 128
donkey.usage.reasoning_tokens     = 96
```

The three `donkey.usage.*` counts carry the cost-relevant detail tokens the
semconv has no pinned key for — cached / cache-write prompt tokens and
reasoning-model thinking tokens. They are read from the response `usage` block's
detail sub-objects and are **omitted, never `0`,** when the provider reports no
detail counts. When the response passes through the SDK's shared HTTP client,
the same counts are exposed per-call on `donkey.last_call`.

Export goes over OTLP to wherever you already send spans. **Nothing in the
emit path is Anypoint-specific.**

That is the point: if your team already runs Langfuse, Datadog, or Phoenix,
then "policy refusals per hour by type" and "tokens per ticket" show up in the
dashboard you already have, with no new tooling to adopt.

### Zero-config export

  **Shipped in Phase 1** (#194). Set the standard OpenTelemetry endpoint env
  var and spans flow — **no SDK-specific variable**.

```bash
pip install "donkey-kit[otel]"
export OTEL_EXPORTER_OTLP_ENDPOINT=https://langfuse.acme.internal
export OTEL_SERVICE_NAME=support-triage       # standard OTel var, honoured for free
python -m my_app                              # spans flow, refused calls included
```

`Donkey.from_env()` reads `OTEL_EXPORTER_OTLP_ENDPOINT` (or the traces-specific
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`) and, when one is set, installs an OTLP
exporter behind a batch processor. The network flush runs on that background
thread, off your request path — which is how instrumentation stays under the
1&nbsp;ms/call bar. The `[otel]` extra ships the **http/protobuf** exporter;
`OTEL_EXPORTER_OTLP_PROTOCOL=grpc` is honoured only if you also install
`opentelemetry-exporter-otlp-proto-grpc`.

**With no endpoint set, the export path is inert and silent** — no exporter is
built, nothing connects, nothing is printed. And if your process already
configures its own OpenTelemetry provider (say via `opentelemetry-instrument`),
Donkey rides it rather than replacing it, so your spans flow through the
pipeline you already set up.

Opt out of telemetry entirely with a single flag:

```bash
export DONKEY_TELEMETRY=false      # or telemetry = false in .donkey-kit.toml
```

### Two honest caveats

  **The GenAI conventions are still `Development` status upstream.** Attribute
  names can change. So the semconv version is **pinned**, and spans are
  **dual-emitted**: `gen_ai.*` at the pinned version, plus a stable `donkey.*`
  namespace under this project's control. Your dashboards do not break when
  upstream renames something.

Second: whether Anypoint Monitoring or Agent Visualizer **ingests** OTLP GenAI
spans is not publicly documented. So this page promises *"exports OTLP"* and
lets the sink be your choice. It does not promise your spans appear in Agent
Visualizer, because that has not been confirmed — see
[Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md).

### Message content stays off spans by default

Spans carry **metadata only** — model, token counts, policy decision, cost tags,
correlation id. They do **not** carry prompt or completion text. This is a
deliberate boundary, not an omission:

  **Spans are emitted upstream of the gateway's PII masking.** The Omni Gateway
  masks sensitive content in *its* logs; a Donkey span is created inside your
  process, before the request reaches the gateway. Putting message text on the
  span would re-export the very content the platform masks — straight to
  whatever OTLP collector you have wired up. So capturing content is **opt-in,
  and opting in is you assuming that obligation.**

Turn it on only when your collector is a trusted sink and you have accepted
responsibility for the content that lands there:

```python
# kwarg, or DONKEY_TELEMETRY_CAPTURE_CONTENT=true, or
# telemetry_capture_content = true in .donkey-kit.toml
donkey = Donkey.from_env(telemetry_capture_content=True)
```

`telemetry_capture_content` resolves along the standard precedence
(kwarg → env → `.donkey-kit.toml` → default) and **defaults to `False`**. When
enabled, content is emitted under the pinned semconv attribute names —
`gen_ai.prompt` and `gen_ai.completion` — and no others. When off (the default),
those attributes never reach a span, and the allowlist that builds every span
drops any content-shaped attribute a call site hands it, so there is no accidental
path for message text to leak.

## Routing & resilience

  **Shipped in Phase 1** (#309). Routing, fallback, and served-model signals
  are available through the shared transport and on OpenTelemetry spans.

The gateway can fail over between providers when one degrades ("Enhanced
Resilience for Intelligent Routing"). It reports what it *did* with each request
on the response — which provider and model served it, how it routed, and whether
that was a **fallback**. Donkey reads those signals off the shared transport, so
you get them with **no framework required** — the raw `donkey.llm.client()` path
benefits just as the deep adapters do.

Every governed call through the SDK's shared HTTP client exposes them on
`donkey.last_call`, beside the usage and identity fields:

```python
donkey = Donkey.from_env()
await donkey.openai().responses.create(model="gpt-5.1", input="…")

r = donkey.last_call
r.requested_model    # "gpt-5.1"  — what you asked for
r.served_model       # "gpt-5.1"  — or a substitute after failover
r.served_provider    # "openai"
r.routing_type       # "ModelBased"
r.fallback           # False      — True if the gateway failed over
r.substituted        # False      — served_model != requested_model
```

They also land on the span (`gen_ai.response.model`, `donkey.routing.type`,
`donkey.routing.fallback`) — the single most useful thing to have on hand when
latency spikes: it tells an operator whether a slow call was routed normally or
recovered from a degraded provider.

### When `last_call` is unavailable

`donkey.last_call` is populated only when the governed response passes through
the SDK's shared httpx client. Four `connection_kwargs()`-only adapters route
outside that response path: ADK and CrewAI use LiteLLM's transport, while
LlamaIndex and Microsoft Agent Framework receive only `default_headers`.
That static snapshot deliberately excludes the correlation ID bound later by
`donkey.run(id=...)`, so those two adapters also carry the asserted
`correlation_id_propagated` exemption.

When every adapter resolved on a `Donkey` is one of those four, a cold read
reports the limitation explicitly. For a `Donkey` that resolved only ADK:

```python
r = donkey.last_call
r.status       # LastCallStatus.UNAVAILABLE
r.available    # False
r.surface      # "adk"
```

This is different from `UNOBSERVED`, which means the current context has not
yet received a governed response. On an unavailable surface the SDK cannot
observe any response-derived `last_call` field, including gateway identity,
routing, fallback, and usage. If multiple non-observing adapters were resolved,
`surface` lists their names. Each limitation is asserted as the
`gateway_identity_observed` conformance exemption rather than silently skipped.

### Two behaviours worth knowing

**The SDK never double-retries a fallback.** Donkey retries `502/503/504` with
backoff, but if the gateway already failed over internally, a `503` it marked as
a fallback is **not** retried again — a second recovery layer stacked on a
working first one just multiplies latency against an outage the gateway already
handled.

**Opt in to model determinism.** A silent substitution is surfaced passively on
`last_call.substituted` by default. When a substitution is not acceptable — your
evaluation, cost model and token assumptions are all pinned to one model — opt
into a hard error:

```python
donkey = Donkey.from_env(on_model_substitution="raise")
# raises ModelSubstituted when the served model differs from the requested one
```

`on_model_substitution` resolves along the standard precedence (kwarg → env
`DONKEY_ON_MODEL_SUBSTITUTION` → `.donkey-kit.toml` → default) and **defaults to
`"off"`**.

  **TypeScript parity is planned (Phase 5).** The same signals will surface as
  `donkey.lastRouting.servedModel` / `.fallback` once the TypeScript SDK ships.

## Correlation IDs

  **Shipped in Phase 1** (#195). See
  [Observability](https://donkey-development-kit.github.io/donkey-development-kit/concepts/observability.md) for the full detail.

Set a per-**run** id once, and every call inside the block carries it — on the
wire, on every span, and on every exception — with nothing threaded through your
framework state:

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

Propagation is contextvar-based, so it reaches through framework nodes (every
LangGraph node, for instance) without threading an argument through every
function, and concurrent runs never leak into each other. It works with or
without OpenTelemetry installed. `donkey.run(...)` is a **dual sync/async**
context manager (plain `with` works too); nested blocks rebind then restore.

### The decorator form

When a whole function should be one run, `@donkey.governed` is the decorator
equivalent of wrapping its body in `donkey.run()`:

```python
@donkey.governed(team="support")
async def handle_ticket(ticket):
    await triage_agent.run(ticket)
```

Each call opens its own run — a fresh run/correlation id (a "run of one") — and
binds the optional per-run cost tags, the OTel span, and typed refusals, exactly
the scope `donkey.run()` establishes. It wraps **both sync and async** callables
and is usable bare (`@donkey.governed`) or parametrised. There is deliberately
**no `id=`**: pinning one id across every call would collapse unrelated runs into
a single correlation, so when you need a specific id, reach for `donkey.run(id=…)`
directly.

  **Verified (#522).** The gateway **reads** the inbound `X-Correlation-Id` and
  echoes it verbatim on the response, so the request and response
  `x-correlation-id` are the same value — the client→gateway join key is real.
  `X-Donkey-Request-Id` is a **client-owned** per-call id the gateway does not
  consume; it lives on `DonkeyError.call_id`. Both names stay overridable
  (`correlation_header` / `call_id_header`) for a gateway that expects different
  ones ([Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)).

## Cost-attribution tags

  **Shipped in Phase 1** (#196). Completes the six-piece minimum alongside the
  correlation IDs above.

A small, fixed set of tags — `team`, `project`, `env`, `enduser.id` — set once
and emitted on every call, both as request headers and as `donkey.cost.*` span
attributes:

```python
donkey = Donkey.from_env(team="support", project="triage-v2", env="prod")

async with donkey.run(id=ticket.id, enduser_id=agent_user.id):
    await triage_agent.run(ticket)
```

The tags resolve along the standard precedence — `Donkey.from_env(team=…)`
kwargs, then `DONKEY_COST_*` env vars, then a `[donkey.cost]` table in
`.donkey-kit.toml`. Per-run overrides layer on top: `donkey.run(team=…,
project=…, env=…, enduser_id=…)` wins **per field** for its block and the rest
fall back to the configured tags. The key set is **fixed** — an unknown
dimension is a configuration error, never a silently-dropped header.

### The question this answers

Finance asks what the support agent cost last month versus the HR bot.

Without tags, both agents share one `client_id`, and the honest answer is
*"we don't know."* With tags it is a group-by.

And for compliance: *"prove the HR bot's answer to user X on date Y went
through the content-safety policy."* The correlation ID on the log line joins
to the gateway record, and the span carries `enduser.id` and
`donkey.policy.type`. That is what an EU AI Act Article 12 log request looks
like in practice — one query, not an investigation.

  **Verified negative (#522).** The Anypoint LLM Gateway has no inbound cost-tag
  ingestion — it meters cost from token usage per API instance and consuming
  client application, not from a client header. So the `X-Anypoint-Cost-*`
  request-header names are a forward-looking convention (harmless — nothing reads
  them); the **authoritative** carrier is the `donkey.cost.*` OTel span attribute,
  which this SDK controls end to end. The names stay overridable (`cost_*_header`)
  for a gateway that does read one.

The tags are **validated** — fixed keys, bounded length — so nobody stuffs a
JSON blob into a header.

## Acceptance bar

- Zero-config: `Donkey.from_env()` plus `OTEL_EXPORTER_OTLP_ENDPOINT` produces
  spans, with no SDK-specific environment variable.
- A **refused** request still produces a span, with
  `donkey.policy.decision=refuse` and `otel.status_code=ERROR`.
- A streaming response produces **exactly one** span, with token counts filled
  in at stream end.
- Opt-out behind a single flag, and under 1 ms of overhead — benchmarked in CI,
  not asserted in prose.
- Every `DonkeyError` exposes `.correlation_id` (the run id) and `.call_id` (the
  per-call id), each matching the header sent.

---

**Status: Phase 1 — OpenTelemetry GenAI spans and OTLP export, per-run
correlation IDs, cost-attribution tags, and routing and resilience signals are
shipped. Platform-facing verification caveats are called out above.**

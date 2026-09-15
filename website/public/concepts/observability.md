# Observability

Every governed model call the SDK makes opens **one OpenTelemetry span**, and
that span carries **two namespaces at once**:

- **`gen_ai.*`** — the OpenTelemetry [GenAI semantic
  conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/), so your
  existing OTel tooling recognises the call as a model call with no custom
  mapping.
- **`donkey.*`** — a stable Agent Fabric namespace for the governance facts OTel
  has no key for yet: the policy decision, the refusal type, the remaining token
  budget, the run correlation id.

Both land on the **same span** — never two spans for one call — so a trace reads
as one governed model call, not a generic call plus a separate governance event.

  Telemetry is **opt-in and never a hard dependency.** Install it with
  `pip install "donkey-kit[otel]"`. With OpenTelemetry absent, or with
  `telemetry=False` in your config, the span code is an inert no-op — it never
  raises and never becomes a required import.

## The pinned semantic-convention version

The OTel GenAI conventions are still evolving upstream (they live under
`_incubating`), and the installed `opentelemetry.semconv` package tracks the
latest schema — its default drifts release to release. So the SDK does **not**
re-export attribute names from that package. Instead:

- The convention version is pinned in one constant,
  `donkey_kit.core.telemetry.GEN_AI_SEMCONV_VERSION` (currently `1.30.0`).
- The `gen_ai.*` keys are **transcribed literals** at that version.

The payoff: upgrading `opentelemetry-semantic-conventions` in your environment
never silently changes what the SDK emits. Bumping the pin is a deliberate,
single-file edit with a matching [changelog](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/develop/CHANGELOG.md)
entry — not something a transitive dependency bump does behind your back.

## What lands on the span

| Key | Namespace | Source |
| --- | --- | --- |
| `gen_ai.system` | `gen_ai.*` (pinned) | The verified `x-llm-proxy-llm-provider` response header (see [Attribution](https://donkey-development-kit.github.io/donkey-development-kit/concepts/attribution.md)). Omitted — never guessed — when the header is absent. |
| `gen_ai.request.model` | `gen_ai.*` (pinned) | The `model` field of the request body. |
| `gen_ai.usage.input_tokens` | `gen_ai.*` (pinned) | The `usage` block of a buffered response, or the terminal `usage` event of a stream (`input_tokens`, falling back to `prompt_tokens`). |
| `gen_ai.usage.output_tokens` | `gen_ai.*` (pinned) | The `usage` block of a buffered response, or the terminal `usage` event of a stream (`output_tokens`, falling back to `completion_tokens`). |
| `donkey.usage.cached_tokens` | `donkey.*` (stable) | Prompt tokens served from cache, from `usage.input_tokens_details.cached_tokens`. The semconv pins no key for this at the pinned version, so it lives in `donkey.*`. Omitted — never `0` — when the provider reports no detail counts. |
| `donkey.usage.cache_write_tokens` | `donkey.*` (stable) | Prompt tokens written to cache, from `usage.input_tokens_details.cache_write_tokens`. Omitted when absent. |
| `donkey.usage.reasoning_tokens` | `donkey.*` (stable) | Reasoning-model thinking tokens, from `usage.output_tokens_details.reasoning_tokens`. Omitted when absent. |
| `donkey.policy.decision` | `donkey.*` (stable) | `allow` on success; `refuse` on a classified policy refusal (which also sets the span's OTel status to `ERROR`). |
| `donkey.policy.type` | `donkey.*` (stable) | The refusal type slug (e.g. `token_budget`, `pii_detected`) — see [Errors](https://donkey-development-kit.github.io/donkey-development-kit/errors.md). |
| `donkey.budget.remaining` | `donkey.*` (stable) | The remaining token budget after this call, from the `x-token-*` headers. |
| `donkey.correlation_id` | `donkey.*` (stable) | The **run** correlation id actually sent on the request (the `X-Correlation-Id` header) — shared by every call in a `donkey.run()` block. The per-call id is deliberately *not* on the span; it lives on `DonkeyError.call_id` (see below). |

  **The `donkey.*` keys are public API.** Renaming one is a breaking change,
  independent of any `gen_ai.*` version bump. Build dashboards and alerts on them
  with the same confidence you'd give a documented field.

Note what is **not** in that table: prompt and completion **text**. The span
carries metadata only. Message content (`gen_ai.prompt` / `gen_ai.completion`)
is emitted **only** when you set `telemetry_capture_content=true`, because spans
are created upstream of the gateway's PII masking — defaulting it on would
re-export the content the platform just masked. The emitter is allowlist-driven,
so a content-shaped attribute handed in from any call site is dropped unless that
opt-in is set. See [Telemetry & cost](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md) for the obligation you take on
by enabling it.

### `gen_ai.system` is never guessed

The proxy routes to several providers (OpenAI, Azure OpenAI, Gemini, Bedrock,
Anthropic), so `gen_ai.system` is taken **only** from the verified
`x-llm-proxy-llm-provider` response header. If that header is absent on a given
response, the attribute is omitted rather than defaulted — a wrong provider
label is worse than a missing one ([Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md),
§0.3).

## Grouping a run: the run id on the span, the call id on the error

Bind a **run** id once and every governed call inside the block carries it — on
the wire, on the span, and on any exception — with nothing threaded through your
framework state:

```python
async with donkey.run(id=ticket.id):
    await triage_agent.run(ticket)
```

`donkey.run(id=…)` returns a **dual sync/async** context manager, so plain
`with donkey.run(...)` works too. Inside the block:

- The **run id** becomes the `X-Correlation-Id` request header, the span's
  `donkey.correlation_id`, and `DonkeyError.correlation_id`. It is shared by
  every call in the block, so a Slack log line joins to the gateway's own record.
- Each individual request also gets a fresh **per-call id** (the
  `X-Donkey-Request-Id` header), unique per logical request and stable across
  that request's retries. It surfaces on `DonkeyError.call_id` — **not** on the
  span, because the span is already the per-call unit; the run id is what a span
  needs to correlate *across* calls.

Propagation is contextvar-based, so a run id set here reaches framework-spawned
`asyncio` tasks (every LangGraph node, for instance) without you passing an
argument through each function, and concurrent runs never leak into each other.
Nested `donkey.run()` blocks rebind then restore; omitting `id` binds a
generated "run of one". Correlation works with or without OpenTelemetry — the
ids are pure headers and contextvars; the span only decorates when OTel is
present.

  The gateway echoes a `x-correlation-id` on its **response**, which is verified.
  Whether it *reads* an inbound `X-Correlation-Id` (and `X-Donkey-Request-Id`) as
  the join key is not yet confirmed, so those request-header **names** are
  overridable placeholders — set `correlation_header` / `call_id_header` in
  config if your gateway expects different names
  ([Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md), §0.3).

## Streaming, refusals, and exceptions

The span is a lifecycle, not just a bag of attributes. Whatever shape a call
takes, it produces **exactly one span**, and that span closes:

- **A refusal is a failed operation.** When the proxy rejects a call and
  `classify()` maps it to a policy refusal, the span carries `donkey.policy.decision
  = refuse` and the `donkey.policy.type` slug *and* its OTel status is set to
  `ERROR` — so a trace reads a refused call as a failure, not a
  successful-looking span with a `refuse` label buried in its attributes.
- **An exception still closes the span.** A transport error (or a cancelled
  request) that escapes before a response settles marks the span `ERROR` and
  ends it — the lifecycle never leaks an open span on the error path.
- **A stream produces one span, closed when the stream is done.** A streaming
  (SSE) response has no usage on its envelope — the token counts arrive in a
  terminal `usage` event the caller reads long after the request returns. The
  span stays open until the stream finishes and is closed **exactly once**,
  whether you drain it fully, abandon it mid-iteration, or it raises partway
  through. `gen_ai.usage.*` and the `donkey.usage.*` detail counts are populated
  from that terminal event (and merged onto `donkey.last_call` at the same time).

  Streaming token counts appear **only if the stream actually carries a usage
  event.** For OpenAI-style Chat Completions that means requesting it with
  `stream_options={"include_usage": true}`; without it the provider emits no
  usage event, so the counts are omitted rather than guessed
  ([Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md), §0.3).

## What this piece does *not* do yet

This is deliberately the span **contract** — the pinned keys, the dual
namespace, and the span lifecycle — not the whole observability story. Tracked,
not forgotten:

- **Exporter configuration.** Wiring an OTLP exporter/endpoint is
  [#194](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/194); the
  SDK emits spans through whatever `TracerProvider` you have configured.
- **Cost tags.** The fixed `team` / `project` / `env` / `enduser.id` dimensions
  now populate both `donkey.cost.*` span attributes and request headers, set once
  via `Donkey.from_env(team=…)` and overridable per run via `donkey.run(team=…)`
  ([#196](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/196),
  shipped) — see [Telemetry & cost](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md).
- **Prompt/completion content stays off the span.** Message bodies are never
  attached — that privacy guarantee is
  [#306](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/306).

## Where to go next

- [Attribution & cost](https://donkey-development-kit.github.io/donkey-development-kit/concepts/attribution.md) — the `client_id` identity and the
  response headers the spans draw from.
- [Errors](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) — the refusal taxonomy behind `donkey.policy.type`.
- [Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md) — why an unverified value is
  omitted, not guessed.

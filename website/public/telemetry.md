# OpenTelemetry spans

Status: Live. Shipped, and verified against a real governed gateway.

DDK answers two questions about every governed call: *what happened?* and *who
pays for it?* It does so with OpenTelemetry GenAI spans. Per-run correlation
IDs, cost-attribution tags and routing signals each have their own page:
[Correlation IDs](https://docs.donkey-kit.dev/correlation.md), [Cost attribution](https://docs.donkey-kit.dev/cost-attribution.md) and
[Routing & fallback](https://docs.donkey-kit.dev/frameworks/routing.md). For what the SDK itself does along
the way, such as retries and token refreshes, see [Debug logging](#debug-logging).

## OpenTelemetry GenAI spans

Every governed model call produces a span following the OpenTelemetry **GenAI
semantic conventions** — `gen_ai.system`, `gen_ai.request.model`,
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` — plus attributes
for the governance layer that generic instrumentation cannot know about.
`gen_ai.system` is the bare vendor the gateway served (`openai`), even when the
proxy reports a qualified `mulesoft:openai`; `donkey.last_call.served_provider`
keeps the qualified value.

```
donkey.policy.decision      = allow | refuse
donkey.policy.type          = pii_detected | token_budget | request_rate_limit | injection | agent_killed | approval_denied | content_safety | …
donkey.policy.provider      = google-model-armor  # who blocked it (provider-backed refusals only)
donkey.policy.categories    = [rai_dangerous]     # why: the provider's flagged categories
donkey.budget.remaining     = 18450
donkey.correlation_id       = …
donkey.correlation_id.source = adopted       # adopted | minted, inside donkey.run_from_headers() only
donkey.cost.team            = support
donkey.cost.project         = triage-v2
donkey.cost.env             = prod
donkey.cost.enduser.id      = user-42
donkey.routing.type         = ModelBased          # how the gateway routed
donkey.routing.fallback     = false               # did it fail over?
gen_ai.response.model       = gpt-5.1             # the model that actually served
donkey.cache.status         = hit                 # semantic-cache outcome (on a cached proxy)
donkey.cache.score          = 0.9518              # similarity score, hit only
donkey.usage.cached_tokens        = 512   # omitted when the provider reports none
donkey.usage.cache_write_tokens   = 128
donkey.usage.reasoning_tokens     = 96
```

A refusal typed by a [custom policy registration](https://docs.donkey-kit.dev/custom-policies.md) records its
slug verbatim as `donkey.policy.type`, for example `acme_salary_block`, so the
span and your policy's own logs share one string.

A refusal made by an external moderation provider also says **who** blocked the
call and **why**. `donkey.policy.provider` is the provider's stable slug
(`azure-content-safety`, `bedrock-guardrails`, `google-model-armor` or
`crowdstrike-aidr`, whose shape is read from the policy's source and not
captured live), and `donkey.policy.categories` is a string array of the
categories it flagged. They are the same values as `ContentSafetyBlocked.provider`
and `.categories` on the [exception](https://docs.donkey-kit.dev/errors.md). Both are omitted on every other
refusal, such as a PII block or a budget refusal. The categories are the
provider's fixed filter names, not request content, so they are safe to export.
Group refusals by `donkey.policy.provider` to compare how often each provider
blocks you, and by `donkey.policy.categories` to see which categories trip most.

The three `donkey.usage.*` counts carry the cost-relevant detail tokens the
semantic conventions have no pinned key for — cached / cache-write prompt tokens
and reasoning-model thinking tokens. They are read from the response `usage`
block's detail sub-objects and are **omitted, never `0`,** when the provider
reports no detail counts. When the response passes through the SDK's shared HTTP
client, the same counts are exposed per-call on `donkey.last_call`.

A **refused** request still produces a span, with `donkey.policy.decision=refuse`
and `otel.status_code=ERROR`. A streaming response produces **exactly one** span,
with token counts filled in at stream end.

DDK's GenAI spans mark escaping exceptions as `ERROR` without copying exception
messages, tracebacks or chained diagnostics into events or status descriptions.
This holds even with content capture enabled; the original exception still
propagates to the caller. Raw refusal `.response` bodies and the LLM PII
`.gateway_message` remain available for explicit inspection and may contain
blocked values. Host or framework instrumentation has its own recording policy.

A [tool that needs approval](https://docs.donkey-kit.dev/hitl.md) records each step of its approval as a
`donkey.tool.approval` span with one event (`donkey.approval.pending`,
`.approved`, `.rejected` or `.timed_out`). The event names the approver and
carries `donkey.correlation_id`, but never the tool's arguments.

Each [tool discovery](https://docs.donkey-kit.dev/registry/discovery/mcp.md) is a `donkey.tools.discover` span,
and each `donkey.tools.warm()` a `donkey.tools.warm` span. `donkey.discover.cache`
is `hit` when the set came from the cache and `miss` when the registry was read.
`donkey.discover.servers` counts the servers in the set, and
`donkey.discover.left_out` the MCP assets left out as ungoverned. A failed read
sets `error.type` to the exception's class name, never its message.

Export goes over OTLP to wherever you already send spans. **Nothing in the
emit path is Anypoint-specific**, so if your team already runs Grafana, Elastic,
Honeycomb, New Relic, Datadog, Dynatrace, Langfuse or Phoenix, "policy refusals
per hour by type" and "tokens per ticket" show up in the dashboard you already
have. See [Send spans to your observability backend](#send-spans-to-your-observability-backend).

### Outbound A2A spans

  The official A2A SDK has its own instrumentation, independent of DDK's
  `telemetry` setting. It can record response diagnostics with an `ALWAYS_ON`
  host sampler. By default DDK refuses outbound A2A calls when SDK tracing is
  enabled or its state cannot be determined, before registry reads or HTTP.
  Disable SDK instrumentation before importing it with
  `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`; this does not disable DDK spans.
  Alternatively, explicitly set `allow_a2a_sdk_telemetry=True` to accept the
  SDK's diagnostic-recording risk. DDK's ordinary `telemetry=True` is not that
  permission, and `telemetry=False` does not disable the SDK's recorder.

To keep both tracing systems enabled:

```python
donkey = Donkey.from_env(allow_a2a_sdk_telemetry=True)
```

The equivalent configuration is `DONKEY_ALLOW_A2A_SDK_TELEMETRY=true` or
`allow_a2a_sdk_telemetry = true` in the `[donkey]` TOML table. It defaults to
`false`. The check uses the SDK's imported instrumentation state: changing
`OTEL_INSTRUMENTATION_A2A_SDK_ENABLED` after import is too late; restart the
process with the opt-out set. DDK never changes the environment or the global
tracer provider for you. With explicit permission, SDK spans retain their
normal parentage and may export exception messages and stack traces.

[`donkey.a2a.send()` and `stream()`](https://docs.donkey-kit.dev/a2a/call.md) open one
`donkey.a2a.call` CLIENT span covering card discovery and delegation. This is
not a GenAI span: A2A SSE is decoded by the official SDK and never scanned for
model token usage. The shared transport still observes budget headers,
`last_call` and typed HTTP refusals.

The span preserves the enclosing run's correlation ID/source or temporarily
mints one for the operation. It injects W3C `traceparent` and, when present,
`tracestate`, without baggage. A remote DDK inbound `donkey.a2a.task` span can
adopt that parent. Team/project/environment cost tags may be recorded, with
run overrides winning; end-user IDs and raw diagnostics are not recorded on
this span. No LLM cost headers are
sent on the scoped A2A plane.

The stream span remains open until the `async with` context exits. Break,
error and cancellation close local resources; cancellation does not send a
remote cancel request. Failures record only `error.type`, with no exception
message or exception event. `telemetry=False` disables the span and W3C
propagation, but correlation still works.

This behavior is **OFFLINE-VERIFIED**. Successful gateway send and SSE are
NOT live verified by this implementation yet; refused streaming requests
before SSE are not successful streaming evidence.

The [A2A policy inventory](https://docs.donkey-kit.dev/governance/gateway-policies/a2a.md#non-blocking-policies) records
the captured quality headers separately. Successful delegation HTTP responses
record `donkey.a2a.quality.evaluation="yes"`, a finite numeric
`donkey.a2a.quality.score`, and `donkey.a2a.quality.status="completed"` when
present. Missing, malformed or unknown evaluation/status values are omitted;
no score range is inferred. Agent Card GETs and failed HTTP responses do not
contribute quality annotations. An annotation never changes the call outcome.

`donkey.a2a.quality.explanation` can repeat request content. It is omitted by
default and recorded only with `telemetry_capture_content=True`, truncated to
4096 characters. Opt-in permits sensitive content export; it does not sanitize
the explanation. This recording is **OFFLINE-VERIFIED** against captured v0.3
headers and mock send/stream IO, not a live facade or streaming evaluation.
It is new in 0.2.1.

DDK does not send `X-AGENT-ID` on MCP or A2A calls: its effect on
`mulesoft_agent_connection_count` attribution has not been verified live.
No connection-graph or metric guarantee is made. Auth challenge fields, returned task/context IDs and raw refusal
diagnostics are not recorded on the outbound span, even with content capture on.

### Zero-config export

Set the standard OpenTelemetry endpoint env var and spans flow — there is **no
SDK-specific variable**.

```bash
pip install "donkey-kit[otel]"
export OTEL_EXPORTER_OTLP_ENDPOINT=https://langfuse.acme.internal
export OTEL_SERVICE_NAME=support-triage       # standard OTel var, honoured for free
python -m my_app                              # spans flow, refused calls included
```

`Donkey.from_env()` reads `OTEL_EXPORTER_OTLP_ENDPOINT` (or the traces-specific
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`) and, when one is set, sets up an OTLP
exporter behind a batch processor for DDK's own spans. The network flush runs on that background
thread, off your request path, keeping instrumentation overhead under
1&nbsp;ms per call. The `[otel]` extra ships the **http/protobuf** exporter;
`OTEL_EXPORTER_OTLP_PROTOCOL=grpc` is honoured only if you also install
`opentelemetry-exporter-otlp-proto-grpc`.

**With no endpoint set, the export path is inert and silent** — no exporter is
built, nothing connects, nothing is printed.

### DDK leaves the global provider to you

OpenTelemetry lets a process set its global `TracerProvider` only once. So
constructing a `Donkey` **never changes `trace.get_tracer_provider()`** by
default. The exporter above sits on a provider that only DDK's spans use, and
your other instrumentation is unaffected.

- **Your process sets its own provider**, before *or after* `Donkey()` (say via
  `opentelemetry-instrument`, or a `trace.set_tracer_provider(...)` call during
  startup). DDK's spans go to that provider from then on, through the pipeline
  you already set up, and DDK's own exporter goes unused.
- **You want DDK's provider to be the global one**, so other libraries' spans
  use the same exporter. Opt in:

```bash
export DONKEY_TELEMETRY_INSTALL_GLOBAL=true   # or telemetry_install_global = true in .donkey-kit.toml
```

```python
from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig

donkey = Donkey(DonkeyConfig.from_env().with_overrides(telemetry_install_global=True))
```

With the flag on and an endpoint set, the first `Donkey()` installs its provider
as the global one, unless your process already set one. The flag does nothing
without an endpoint.

DDK's own provider flushes its pending spans when the interpreter exits. To
flush earlier, call `donkey.flush_telemetry()`. Use it in a serverless handler
before the runtime freezes the process, or in a script that checks its collector
before it exits:

```python nocheck
def handler(event, context):
    try:
        return run_agent(event)
    finally:
        donkey.flush_telemetry(timeout_s=2.0)
```

It flushes the provider DDK built, scoped or installed as the global by the
flag, and waits up to `timeout_s` (default 2 seconds). It returns `True` once
the spans are exported, or when DDK built no provider, and `False` if the flush
timed out or failed. An export failure never raises. The call blocks, so from
async code use `await asyncio.to_thread(donkey.flush_telemetry)`.

When your process sets its own provider, DDK's spans ride it, and flushing them
is yours: `trace.get_tracer_provider().force_flush()`. `flush_telemetry()`
leaves that provider alone. `trace.get_tracer_provider().force_flush()` does not
reach DDK's scoped provider.

One exit is flushed for you. When the gateway's Agent Kill Switch refuses a call,
DDK force-flushes both DDK's own provider and the global one before
[`AgentKilled`](https://docs.donkey-kit.dev/errors.md) reaches your code, waiting up to 2 seconds for each. So
the kill's own span, and the spans before it, are exported even if the agent exits
straight away. A failed export never replaces the `AgentKilled`.

Opt out of telemetry entirely with a single flag:

```bash
export DONKEY_TELEMETRY=false      # or telemetry = false in .donkey-kit.toml
```

### Send spans to your observability backend

Any backend that accepts OTLP works with the same three standard variables:
`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS` for the vendor's
credentials, and `OTEL_SERVICE_NAME`. There is no DDK-specific setting and no
code change between backends. Pick yours:

Grafana Cloud stores traces in **Tempo**. In the Grafana Cloud portal, open your
stack and choose **Configure** on the **OpenTelemetry** tile. It generates the
endpoint for your region and a token, already base64-encoded as
`instanceID:token`.

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="https://otlp-gateway-prod-<region>.grafana.net/otlp"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic%20<base64 instanceID:token>"
export OTEL_SERVICE_NAME="support-triage"
```

Grafana's own note for Python: write the space after `Basic` as `%20`. Find the
spans in **Explore → Tempo** or in **Application Observability**. For
production, Grafana recommends sending through **Grafana Alloy** (its
OpenTelemetry Collector) rather than straight from the app.

Docs: [Send data to the Grafana Cloud OTLP endpoint](https://grafana.com/docs/grafana-cloud/send-data/otlp/send-data-otlp/)

On Elastic Cloud Hosted or Serverless, copy the **Managed OTLP endpoint** from
Kibana (**Add data → Applications → OpenTelemetry**), which also generates an
API key. Prefix the key with `ApiKey` yourself; Kibana gives you the bare key.

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="https://<motlp-endpoint>"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=ApiKey%20<api-key>"
export OTEL_SERVICE_NAME="support-triage"
```

Spans appear in Kibana under **Observability → Applications**, grouped by
service. Self-managed Elasticsearch exposes the same protocol at
`<es-endpoint>/_otlp`; its API key needs `create_doc` and `auto_configure`
privileges.

Docs: [Send OTLP data to Elastic](https://www.elastic.co/docs/solutions/observability/get-started/quickstart-elastic-cloud-otel-endpoint) ·
[Elasticsearch OTLP/HTTP endpoint](https://www.elastic.co/docs/manage-data/ingest/otlp-endpoint)

Create an ingest API key in your Honeycomb environment settings.

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="https://api.honeycomb.io"   # EU: https://api.eu1.honeycomb.io
export OTEL_EXPORTER_OTLP_HEADERS="x-honeycomb-team=<api-key>"
export OTEL_SERVICE_NAME="support-triage"
```

Honeycomb creates a dataset named after `OTEL_SERVICE_NAME`. Query
`donkey.policy.decision`, `donkey.cost.team` or `gen_ai.usage.output_tokens` like
any other field; for example, a `COUNT` where `donkey.policy.decision = refuse`,
grouped by `donkey.policy.type`, gives you refusals by type.

Docs: [Using the Honeycomb OpenTelemetry endpoint](https://docs.honeycomb.io/send-data/opentelemetry/)

Authenticate with your account's **license key** in an `api-key` header.

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="https://otlp.nr-data.net"   # EU: https://otlp.eu01.nr-data.net
export OTEL_EXPORTER_OTLP_HEADERS="api-key=<license-key>"
export OTEL_SERVICE_NAME="support-triage"
```

New Relic recommends `http/protobuf`, which is DDK's default. The service shows
up under **APM & Services**, and span attributes are queryable with NRQL, for
example `SELECT count(*) FROM Span WHERE donkey.policy.decision = 'refuse' FACET donkey.policy.type`.

Docs: [New Relic OTLP endpoint](https://docs.newrelic.com/docs/opentelemetry/best-practices/opentelemetry-otlp/)

The simplest route is the **Datadog Agent**, which accepts OTLP once its receiver
is enabled. Start the Agent with
`DD_OTLP_CONFIG_RECEIVER_PROTOCOLS_HTTP_ENDPOINT=0.0.0.0:4318` and expose port
`4318`, then point DDK at it. No credentials are needed in the app; the Agent
holds the API key.

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"   # the Agent's host
export OTEL_SERVICE_NAME="support-triage"
```

Spans appear in **APM → Traces**. Datadog also offers an agentless OTLP traces
intake that takes a `dd-api-key` header; its URL depends on your Datadog site.

Docs: [OTLP ingestion by the Datadog Agent](https://docs.datadoghq.com/opentelemetry/setup/otlp_ingest_in_the_agent/) ·
[OTLP traces intake endpoint](https://docs.datadoghq.com/opentelemetry/setup/otlp_ingest/traces/)

Create an access token with the `openTelemetryTrace.ingest` scope.

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="https://<environment-id>.live.dynatrace.com/api/v2/otlp"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Api-Token%20<token>"
export OTEL_SERVICE_NAME="support-triage"
```

Use `live.dynatrace.com`, not `live.apps.dynatrace.com`. Through an ActiveGate the
base URL is `https://<activegate>:9999/e/<environment-id>/api/v2/otlp`. Spans
appear in **Distributed Tracing**.

Docs: [Dynatrace OTLP API endpoints](https://docs.dynatrace.com/docs/ingest-from/opentelemetry/otlp-api)

Langfuse is built for LLM traces, so the `gen_ai.*` model and token attributes
on DDK spans are what it reads. Authenticate with your project's public and
secret key, base64-encoded:

```bash
AUTH_STRING=$(echo -n "pk-lf-...:sk-lf-..." | base64)
export OTEL_EXPORTER_OTLP_ENDPOINT="https://cloud.langfuse.com/api/public/otel"   # US: https://us.cloud.langfuse.com/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic%20${AUTH_STRING},x-langfuse-ingestion-version=4"
export OTEL_SERVICE_NAME="support-triage"
```

The `x-langfuse-ingestion-version=4` header makes spans appear in real time
instead of after a delay of up to ten minutes. Self-hosted Langfuse (v3.22 or
later) takes the same path on your own host.

Docs: [Langfuse OpenTelemetry](https://langfuse.com/docs/opentelemetry/get-started)

Phoenix is open source and runs locally with no account, which makes it a
quick way to look at LLM spans during development:

```bash
docker run -p 6006:6006 -p 4317:4317 arizephoenix/phoenix:latest
```

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:6006"
export OTEL_SERVICE_NAME="support-triage"
```

Open `http://localhost:6006` to browse the spans. A Phoenix deployment with
authentication enabled also needs its API key as a bearer token:
`OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer%20<api-key>"`.

Docs: [Phoenix configuration](https://arize.com/docs/phoenix/self-hosting/configuration) ·
[Get started with tracing](https://arize.com/docs/phoenix/get-started/get-started-tracing)

Jaeger's all-in-one container is the fastest way to see spans on your laptop,
with no account and no credentials:

```bash
docker run --rm --name jaeger -p 16686:16686 -p 4318:4318 \
  cr.jaegertracing.io/jaegertracing/jaeger:latest
```

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"
export OTEL_SERVICE_NAME="support-triage"
```

Open `http://localhost:16686`, pick the `support-triage` service and search.
Storage is in memory, so traces are gone when the container stops.

Docs: [Jaeger getting started](https://www.jaegertracing.io/docs/latest/getting-started/)

A few rules hold for every backend:

- **Base endpoint vs traces endpoint.** `OTEL_EXPORTER_OTLP_ENDPOINT` is a base
  URL: the exporter appends `/v1/traces`. If you set
  `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` instead, give the full URL including
  `/v1/traces`, or the backend answers `404`.
- **Encode spaces in headers.** Header values such as `Basic <token>` or
  `ApiKey <key>` should be written with `%20` in place of the space; separate
  several headers with commas.
- **Protocol.** DDK exports `http/protobuf`, which every backend above accepts.
  For gRPC, set `OTEL_EXPORTER_OTLP_PROTOCOL=grpc` and install
  `opentelemetry-exporter-otlp-proto-grpc`.
- **In production, consider a Collector.** Sending to an
  [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/) (or a
  vendor distribution such as Grafana Alloy) lets you batch, retry, redact and
  fan out to several backends without touching the app.
- **Prompts stay private by default.** None of these backends receives prompt or
  completion text unless you opt in; see
  [Message content stays off spans by default](#message-content-stays-off-spans-by-default).

### Semantic-convention stability and sinks

  **The GenAI conventions are still `Development` status upstream**, so
  attribute names can change. DDK **pins** the semconv version and
  **dual-emits**: `gen_ai.*` at the pinned version, plus a stable `donkey.*`
  namespace under this project's control. Your dashboards do not break when
  upstream renames something.

DDK exports standard OTLP and leaves the sink to you. Whether Anypoint
Monitoring or Agent Visualizer ingests OTLP GenAI spans is not publicly
documented, so don't rely on spans appearing there.

### Message content stays off spans by default

Spans carry **metadata only** — model, token counts, policy decision, cost tags,
correlation id. They do **not** carry prompt or completion text.

  **Spans are emitted upstream of the gateway's PII masking.** The Omni Gateway
  masks sensitive content in *its* logs; a DDK span is created inside your
  process, before the request reaches the gateway. Putting message text on the
  span would re-export the very content the platform masks — straight to
  whatever OTLP collector you have wired up. So capturing content is **opt-in,
  and opting in is you assuming that obligation.**

Turn it on only when your collector is a trusted sink and you have accepted
responsibility for the content that lands there:

```bash
export DONKEY_TELEMETRY_CAPTURE_CONTENT=true
# or: telemetry_capture_content = true under [donkey] in .donkey-kit.toml
```

In code, pass it to `Donkey.from_env()`, which takes any `DonkeyConfig` field as
a keyword argument:

```python
from donkey_kit import Donkey

donkey = Donkey.from_env(telemetry_capture_content=True)
```

`telemetry_capture_content` resolves along the standard
[precedence](https://docs.donkey-kit.dev/reference/configuration.md#precedence) (set in code → env → config
files → default) and **defaults to `False`**. When
enabled, LLM content is emitted under the pinned semconv attribute names
`gen_ai.prompt` and `gen_ai.completion`. New in 0.2.1, it also permits outbound
A2A quality explanations under `donkey.a2a.quality.explanation`, limited to
4096 characters. Gateway explanations may repeat request text and are exported
without sanitization; truncation is not redaction. This A2A behavior is new in 0.2.1.

When off, prompt/completion attributes are excluded by the LLM span allowlist
and A2A quality explanation text is omitted by the outbound recorder. Numeric
quality scores and the captured evaluation/status values do not require content
opt-in. See [Outbound A2A spans](#outbound-a2a-spans).

## Debug logging

Spans tell you what a call did. When you need to see what the SDK did *for*
you along the way, such as a retry, a token refresh or a fallback, turn on its
DEBUG logging. It uses the standard library `logging` module, with one logger
per module under the `donkey_kit` package logger:

```python
import logging

logging.basicConfig()                                   # or your own handlers
logging.getLogger("donkey_kit").setLevel(logging.DEBUG)
```

A retried 503 then reads:

```text
DEBUG:donkey_kit.core.transport.observe:GET https://proxy.example/v1/chat/completions returned 503; retry 1 of 3 in 0.48s
```

The SDK attaches only a `NullHandler` to `donkey_kit` and never configures a
handler, level or format of its own, so nothing prints until your application
does. That also means you can narrow it. Set the level on one module logger,
such as `donkey_kit.core.auth`, instead of the package.

| What is logged at DEBUG | Logger |
| --- | --- |
| Each retry: the status, which retry it is, and the delay | `donkey_kit.core.transport.observe` |
| A retryable status that is not retried: after a gateway routing fallback, a 502/504 on a model call (see [Retry behaviour](https://docs.donkey-kit.dev/errors.md#retry-behaviour)), or once retries run out | `donkey_kit.core.transport.observe` |
| A 401 on the async client, followed by one token refresh and re-send | `donkey_kit.core.transport.async_client` |
| A connected-app token fetch (the token URL only) and token invalidation | `donkey_kit.core.auth` |
| An auth provider in a `ChainedAuth` failing over to the next one (type names only) | `donkey_kit.core.auth` |
| The OTLP export bootstrap: inert, kept your `TracerProvider`, or installed (global or DDK-scoped) | `donkey_kit.core.telemetry` |
| How many tool descriptors a `ToolSet` exposes after its filter, out of the total | `donkey_kit.tools.session` |
| A rate-limit reset header that was ignored as negative or out of range, naming the budget window (never the value) | `donkey_kit.core._window` |
| A refusal translator that failed and was skipped, or a refusal capture that could not tag a framework error (type names only, no hook representation or traceback) | `donkey_kit.core.refusals` |
| Other best-effort steps that failed and were skipped (with the traceback), e.g. recording usage on a span | the module that skipped it |

  **A log record never carries a credential.** Records name a request by
  method, scheme, host and path only. They never include a header value,
  token, client secret, query string or request body, and an auth failure is
  logged by exception type, not message. A unit test pins this against every
  credential the SDK sends: the bearer token, client id and client secret.

If every `ChainedAuth` provider fails, the final `AuthError` names only the
last exception type, not its message, and suppresses exception chaining.
Refusal-handler failures likewise omit callback messages and tracebacks; the
`donkey_kit.core.reactions` logger records only the handler label and exception
type at `WARNING`, and the original refusal still propagates.

The SDK does not use WARNING log records for configuration problems. A one-time
problem you have to fix is a Python warning instead, raised through
`warnings.warn` with its own category, so you can filter it or make it an
error with the `warnings` module. For example, an OTLP endpoint set without its
exporter installed raises `donkey_kit.core.telemetry.TelemetryExportWarning`
(see [Zero-config export](#zero-config-export)).

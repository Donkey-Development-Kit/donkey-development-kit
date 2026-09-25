# Telemetry

Platform teams ask for two things agent teams rarely deliver: a trace that
follows one logical run across every model call it fans out into, and spans in
the standard GenAI vocabulary so they land in existing dashboards. Both come
from the same client every request leaves through. Each governed call emits a
span carrying `gen_ai.*` and `donkey.*` attributes, `donkey.run(id=…)` ties a
whole run to one correlation id and one set of cost tags, and setting
`OTEL_EXPORTER_OTLP_ENDPOINT` is enough for `Donkey.from_env()` to install an
exporter.

| Example | Shows | Needs |
| --- | --- | --- |
| Narrative demo 06 | One span per call with both namespaces and routing/usage, three calls under one run id and cost tags, a refused call as an `ERROR` span, and zero-config OTLP | `[otel]` (simulator) |
| OpenAI script 06 | A host-owned `TracerProvider` exporting over OTLP; Donkey rides it | Proxy credentials + an OTLP endpoint |
| OpenAI script 07 | Several `donkey.run(team=…, project=…)` blocks, including a refusal span | Proxy credentials + an OTLP endpoint |
| OpenAI script 10 | `Donkey.from_env()` installing OTLP itself when the env var is set | Proxy credentials |

## Run it

```bash
pip install "donkey-kit[otel]"
make demo N=06
```

```text
════════════════════════════════════════════════════════════════════════════════════════
Demo 06 — OTel GenAI spans and correlation ids
Standard GenAI telemetry and one trace per run, without instrumentation code.
════════════════════════════════════════════════════════════════════════════════════════

Run context
───────────
  target                 mock
  proxy base_url         http://127.0.0.1:8080/
  output masking         on
  credentials            fake — the simulator enforces no auth

[1] One governed call, one GenAI span — with no instrumentation code

    # your usual OTel setup, then:
    await client.responses.create(model=..., input=...)

  span                   donkey.llm.chat
  status                 UNSET
  gen_ai.*
    gen_ai.request.model        gpt-4o
    gen_ai.system               openai
    gen_ai.response.model       gpt-5.1
    gen_ai.usage.input_tokens   17
    gen_ai.usage.output_tokens  51
  donkey.*
    donkey.routing.type              ModelBased
    donkey.routing.fallback          False
    donkey.usage.cached_tokens       0
    donkey.usage.cache_write_tokens  0
    donkey.usage.reasoning_tokens    0
    donkey.policy.decision           allow
    donkey.budget.remaining          95000
    donkey.cost.team                 platform
    donkey.cost.env                  dev
    donkey.correlation_id            a8fdd39a06f145f3b20520cd9ee129fc

  PASS  gen_ai.prompt / gen_ai.completion are absent — capture is opt-in

  The gen_ai.* keys are pinned to semantic-convention version 1.30.0. They are
  transcribed in the SDK rather than imported from the semconv package, whose default
  version drifts release to release — so what lands on your span is decided by a
  reviewable edit, not by a transitive upgrade.
  Prompt and completion stay off the span unless you set telemetry_capture_content=True
  (or DONKEY_TELEMETRY_CAPTURE_CONTENT=1). The gateway masks PII in its logs; spans are
  emitted upstream of that, so defaulting capture on would re-export the content the
  platform just masked.

Routing and usage, on the same span
───────────────────────────────────
  routing
    gen_ai.request.model     gpt-4o
    gen_ai.response.model    gpt-5.1
    donkey.routing.type      ModelBased
    donkey.routing.fallback  False
  donkey.usage.*
    donkey.usage.cached_tokens       0
    donkey.usage.cache_write_tokens  0
    donkey.usage.reasoning_tokens    0

  gen_ai.response.model is what the gateway actually served. When it differs from
  gen_ai.request.model, a failover happened — the fastest read on a latency spike.
  donkey.routing.fallback is emitted even when False: 'we routed normally' is a signal,
  not the absence of one. The same facts live on donkey.last_call without a span backend
  (demo 10).

[2] One correlation id for a whole run, however many calls it makes

    async with donkey.run(id=ticket.id, team="support", project="triage"):
        await client.responses.create(...)     # all three calls share
        await client.responses.create(...)     # one id, and the cost
        await client.responses.create(...)     # tags, on wire and spans

  run id                 ticket-4417
  spans emitted          3
  distinct correlation ids 1
  PASS  all 3 spans carry the one run id
  PASS  run() overrode team/project; env inherited from from_env()

  Nothing was threaded through the agent. The id is bound to the async context, and
  tasks the framework spawns copy that context — so a LangGraph node running the model
  on a child task is inside the same run without knowing the run exists. Concurrent runs
  do not leak into each other, and nested run() blocks rebind then restore. Cost tags
  ride the same context: run(team=..., project=...) overrides those dimensions for the
  block and inherits the rest from from_env().

Two ids, two questions
──────────────────────
  run    header          X-Correlation-Id
  call   header          X-Donkey-Request-Id
  The run id answers 'show me everything this ticket did'. The per-call id answers
  'which one of those calls was this'. Both go out on every request, which is what lets
  a line in your log join to the gateway's own record of the same call.

[3] A refusal is a failed span, not a successful-looking one
  A span that ends OK on a request the gateway refused is worse than no span: it makes a
  dashboard say everything is fine. So a refusal sets the span status to ERROR and
  records what refused it.

  span                   donkey.llm.chat
  status                 ERROR
  gen_ai.*
    gen_ai.request.model   donkey-sim/pii-detected
    gen_ai.system          openai
    gen_ai.response.model  gpt-5.1
  donkey.*
    donkey.routing.type      ModelBased
    donkey.routing.fallback  False
    donkey.policy.decision   refuse
    donkey.policy.type       pii_detected
    donkey.budget.remaining  1
    donkey.cost.team         platform
    donkey.cost.env          dev
    donkey.correlation_id    ticket-4417

  donkey.policy.decision is 'refuse' and donkey.policy.type names the specific policy —
  so a dashboard can separate 'the model failed' from 'governance said no', which are
  very different operational stories.

The same two ids, on the exception
──────────────────────────────────
  type                   PIIDetected
  .correlation_id        ticket-4417
  .call_id               c37c6199f07c43fe9f0555fd88cc4cdd
  classify() read those back off the request the failed response came from, so the
  exception you catch already carries the ids without you passing them in. Put
  .correlation_id in the alert and the platform team can pull the gateway's record of
  the same refusal.

[4] Zero-config OTLP: set the standard env var, or stay silent

    # no Donkey-specific variable
    export OTEL_EXPORTER_OTLP_ENDPOINT=https://…
    donkey = Donkey.from_env()   # installs OTLP behind a BatchSpanProcessor
    # no endpoint → inert, silent, nothing connects
    # DONKEY_TELEMETRY=false    → opt out even if an endpoint is set

  PASS  no OTEL_EXPORTER_OTLP_ENDPOINT — export stayed inert and silent
  Donkey.from_env() installs OTLP only when that standard env var is set. It will not
  clobber a TracerProvider the host already installed — which is why this demo's in-
  memory table still works. Opt out with DONKEY_TELEMETRY=false (or telemetry = false in
  .donkey-kit.toml). Cost tags on donkey.run(team=..., project=...) are what let a
  backend slice refusals, budget and latency by agent without another attribute
  convention.

The point
─────────
  Span name is 'donkey.llm.chat'. Nothing in the agent code above mentions OpenTelemetry
  — the instrumentation hangs off the same transport hooks as the budget and the typed
  refusals, which is why they all landed in one milestone rather than three.
  Cost tags are the fixed four — team / project / env / enduser.id — set on from_env()
  and overridable per donkey.run(). They land on donkey.cost.* whether or not the
  gateway-side header names are verified yet. Routing (donkey.routing.*) and
  cached/reasoning usage (donkey.usage.*) land on the same span. Zero-config OTLP is
  shipped: OTEL_EXPORTER_OTLP_ENDPOINT, otherwise silent.

────────────────────────────────────────────────────────────────────────────────────────
```

```bash
python "demos/human-made/openai/06 - otel exporter simple.py"     # proxy + OTEL_EXPORTER_OTLP_ENDPOINT / _HEADERS
python "demos/human-made/openai/07 - otel exporter advanced.py"   # proxy + OTEL_EXPORTER_OTLP_ENDPOINT / _HEADERS
python "demos/human-made/openai/10 - zero-config-otlp.py"         # proxy; set OTEL_EXPORTER_OTLP_ENDPOINT to export
```

Without `[otel]` installed, narrative demo 06 prints the install command and
exits cleanly. It installs an in-memory exporter so it can print the spans as a
table.

## Key code

One correlation id and one set of cost tags for a whole run (narrative demo 06,
act 2):

```python
async with donkey.run(id=ticket.id, team="support", project="triage"):
    await client.responses.create(...)     # all three calls share
    await client.responses.create(...)     # one id, and the cost
    await client.responses.create(...)     # tags, on wire and spans
```

Your own `TracerProvider`, with several runs and a refusal (OpenAI script 07):

```python
provider = TracerProvider(resource=Resource.create({"service.name": "donkey-dev-kit"}))
provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)

donkey = Donkey.from_env()
client = donkey.openai(sync=True)

with donkey.run(id="agent-greeter", team="cx", project="welcome"):
    reply = client.responses.create(model=MODEL, input="Say hello in exactly three words.")
    print("greeter:", reply.output_text)

with donkey.run(id="agent-support", team="cx", project="tickets"):
    try:
        client.responses.create(model=MODEL, input=PII_PROMPT)
        print("support: no refusal")
    except openai.APIStatusError as err:
        error = classify(err.response)
        print("support:", type(error).__name__, getattr(error, "entities", None))

provider.force_flush()
donkey.close()
```

Zero-config export (OpenAI script 10) needs no provider setup at all:

```python
donkey = Donkey.from_env()
client = donkey.openai(sync=True)

with donkey.run(id="otel-zero-config", team="cx", project="welcome"):
    reply = client.responses.create(model="gpt-4o", input="Say hello in exactly three words.")
    print(reply.output_text)
```

  `gen_ai.prompt` and `gen_ai.completion` stay off the span unless you set
  `telemetry_capture_content=True` (or `DONKEY_TELEMETRY_CAPTURE_CONTENT=1`).
  Spans are emitted upstream of the gateway's PII mask, so capturing by default
  would re-export content the platform just masked.

- **Two ids per request.** The run id (`X-Correlation-Id`) answers "everything
  this ticket did"; the per-call id (`X-Donkey-Request-Id`) answers "which call
  was this". A caught refusal carries both as `.correlation_id` and `.call_id`.
- **Refusals are failed spans.** A refused call sets the span status to
  `ERROR` and records `donkey.policy.decision=refuse` with the specific
  `donkey.policy.type`.
- **Cost tags are a fixed four** — `team`, `project`, `env`, `enduser.id` —
  set on `from_env()` and overridable per `run()`, landing on `donkey.cost.*`.
- **Export is opt-in.** No endpoint means inert and silent;
  `DONKEY_TELEMETRY=false` opts out even when one is set, and a host
  `TracerProvider` is never replaced.

**Learn more:** [Telemetry & cost](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md)

**Source:**
[narrative demo 06](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/claude-made/06_telemetry) ·
[script 06](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/blob/main/demos/human-made/openai/06%20-%20otel%20exporter%20simple.py) ·
[script 07](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/blob/main/demos/human-made/openai/07%20-%20otel%20exporter%20advanced.py) ·
[script 10](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/blob/main/demos/human-made/openai/10%20-%20zero-config-otlp.py)

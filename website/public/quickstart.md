# Quickstart

Make your first **governed** model call and see the three things a bare
`base_url` + headers can't give you — a **typed refusal**, a **budget** object,
and a **span** — in under 15 minutes, with **no Anypoint credentials** and **no
real gateway**.

You'll run the SDK against the bundled **local gateway simulator**: a
pure-Python stand-in that replays the *same* captured rejection fixtures the
SDK's typed errors are tested against, so refusals light up locally exactly as
they would against a live proxy.

  The simulator is a **fixture replay, never a real gateway** (BG §1.4). It
  enforces no policy and forwards no traffic, and every response it serves
  carries `x-donkey-simulator: true` so it can't be mistaken for one. It also
  **ignores auth** — that's why the credentials below are throwaway
  placeholders. See [Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md).

### Install

Install the base SDK, the raw LLM client, the simulator, and OpenTelemetry (so
you can see a span):

```bash
pip install "donkey-kit[llm,local,otel]"
```

- `llm` — the OpenAI client `donkey.llm.client()` hands back.
- `local` — the local gateway simulator (`starlette` + `uvicorn`).
- `otel` — the OpenTelemetry SDK, so the `donkey.llm.chat` span is emitted.

### Start the local gateway

In one terminal, boot the simulator. It binds `127.0.0.1:8080` and stays in the
foreground:

```bash
donkey mock
```

It serves the captured fixtures over a real TCP port — the exact surface a stock
client points `DONKEY_LLM_PROXY_URL` at.

### Point the SDK at it

In a second terminal, set the three governed-access values. `Donkey.from_env()`
requires the `client_id` / `client_secret` header pair to *exist*, but the
simulator never checks it — so these are placeholders, not real credentials:

```bash
export DONKEY_LLM_PROXY_URL="http://127.0.0.1:8080"
export DONKEY_LLM_PROXY_CLIENT_ID="local"       # placeholder — simulator ignores auth
export DONKEY_LLM_PROXY_CLIENT_SECRET="local"   # placeholder — simulator ignores auth
```

  **No Anypoint credentials at any step.** This is the whole point of the
  gateway-free path: you see the SDK's governance surfaces work before you ever
  obtain a single credential.

### See a span and a budget

Wire a `ConsoleSpanExporter` so spans print to your terminal, then make one
governed call. `donkey.llm.client(sync=True)` returns a real `openai.OpenAI`,
but routed through the Donkey transport — which is what makes the call
auto-emit a span and update the budget:

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

from donkey_kit import Donkey

# Send spans to the console. core.telemetry reads the global provider when it
# opens each span, so set this before the first call.
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(provider)

with Donkey.from_env() as donkey:
    client = donkey.llm.client(sync=True)      # a real openai.OpenAI, governed
    reply = client.responses.create(model="gpt-5.1", input="Say hi in three words.")
    print(reply.output_text)
    print("budget remaining:", donkey.budget.remaining, "tokens")
```

You'll see a `donkey.llm.chat` span printed with `gen_ai.usage.*` token counts,
`donkey.policy.decision` (`allow`), `donkey.budget.remaining`, and the
correlation id. And `donkey.budget` is a real
[`Budget`](https://donkey-development-kit.github.io/donkey-development-kit/feature-overview.md) object updated from the response's rate-limit
header — not a header you parse yourself.

### Catch a typed refusal

The simulator serves any of its captured rejection shapes on demand: a request
whose model id is `donkey-sim/<shape>` is served that exact rejection body.
Ask for the PII shape, and bridge the raw response into the taxonomy with
`donkey_kit.core.errors.classify()`:

```python
import openai
from donkey_kit import PIIDetected
from donkey_kit.core.errors import classify

try:
    client.responses.create(
        model="donkey-sim/pii-detected",
        input="My SSN is 123-45-6789, please store it.",
    )
except openai.APIStatusError as exc:
    governed = classify(exc.response)          # bridge into the taxonomy
    if isinstance(governed, PIIDetected):
        print("blocked, entities:", governed.entities)
```

  `donkey.llm.client()` gives you the framework's own client, so a refusal
  arrives as `openai.APIStatusError`, **not** a `DonkeyError` — until you bridge
  it with `classify()`. The framework adapters (`donkey.langgraph`, …) do this
  for you; see the [Error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md).

  **Prefer to run it all at once?** The whole flow above is a single runnable
  script — `examples/quickstart/main.py` — that boots the simulator for you and
  needs no environment setup. It's executed and timed on a clean machine in CI,
  so this quickstart can never silently rot.

## When you're ready: point at your real gateway

The *same code* runs against your Omni Gateway proxy — you only change the
environment. Stop the simulator and set the three real governed-access values.
The proxy authenticates on a `client_id` / `client_secret` **header pair**
(consumer auth) — **not** a bearer token, and separate from any Anypoint
control-plane credential:

```bash
export DONKEY_LLM_PROXY_URL="https://<ingress-gw>/<instance>/"   # note: no /v1
export DONKEY_LLM_PROXY_CLIENT_ID="<consumer client id>"
export DONKEY_LLM_PROXY_CLIENT_SECRET="<consumer client secret>"
```

Nothing in the Python changes — same `Donkey.from_env()`, same client, same
typed refusals, same span and budget. From here:

- **[Pick your framework](https://donkey-development-kit.github.io/donkey-development-kit/frameworks.md)** — get your framework's own native model
  object (LangGraph, ADK, Strands, LlamaIndex, CrewAI, the OpenAI Agents SDK,
  the Anthropic SDK, Agent Framework) instead of the raw client, in three lines.
  The proxy is OpenAI-compatible, so any OpenAI-compatible TypeScript SDK works
  the same way.

## Where to go next

- **[LangGraph support-triage demo](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/langgraph.md)** — this quickstart
  scaled to a real agent: `examples/langgraph/main.py` triages five support
  tickets through a two-node LangGraph graph against the simulator, blocks the
  PII-laden one as a typed `PIIDetected`, and exports every call's span to a
  bundled local OTLP collector — end to end, still no gateway. Run it with
  `python -m examples.langgraph.main`. It's the Phase-1 acceptance demo, timed in
  CI.
- **[Feature overview](https://donkey-development-kit.github.io/donkey-development-kit/feature-overview.md)** — everything governed model access
  gives you, including `donkey.budget` pacing and the telemetry span contract.
- **[Error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md)** — every rejection shape, its discriminator, and
  the typed exception `classify()` returns for it.
- **[Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)** — what's confirmed against a
  live proxy vs. what raises a clear error, and why the simulator only ever
  replays captured shapes.

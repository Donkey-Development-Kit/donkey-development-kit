# Support triage

A support agent drafts a one-line reply to each ticket in a queue. Most are
routine; one contains a customer's SSN. On a bare `base_url` that PII-laden call
sails straight through to the model. Through a governed proxy it comes back as a
**typed refusal** — and the branch that masks and re-routes it is a branch you
can watch execute *before* it runs on real data.

This is the shipped Phase-1 acceptance demo (`BG §1.8`): `pip install` to a
drafted reply against the [local simulator](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md) only — no Anypoint
credentials, no real gateway — exercising four governance pieces at once.

## What it demonstrates

- **Runs with no gateway.** The demo boots the [local simulator](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md)
  in-process on an ephemeral port and points the SDK at it; the simulator
  ignores auth, so the credentials are throwaway placeholders.
- **The PII-masking branch actually executes.** The simulator runs the
  `pii_block:every=5` [scenario](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md#scenario-scripting) — every fifth
  `POST /responses` is served the captured `pii-detected` **403**. The fifth
  ticket (the one with the SSN) is blocked, and the refusal surfaces out of the
  LangGraph run as a typed [`PIIDetected`](https://donkey-development-kit.github.io/donkey-development-kit/errors.md), not a framework-wrapped
  generic error.
- **Correlation reaches every node for free.** Each ticket runs inside a
  [`donkey.run(id=...)`](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md) block; the run id shows up in a graph
  node's own logs without being threaded through graph state, because LangGraph
  runs nodes on context-copying `asyncio` tasks.
- **Every governed call emits an OTel span** — refusals included — exported to a
  bundled local OTLP collector with [zero config](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md): setting the
  standard `OTEL_EXPORTER_OTLP_ENDPOINT` is all it takes.

## Run it

### Install the extras

```bash
pip install "donkey-kit[langgraph,local,otel]"
```

`langgraph` brings the framework and the adapter, `local` the simulator, `otel`
the OpenTelemetry export path.

### Run the demo

```bash
python -m examples.langgraph.main
```

No environment setup: the demo boots its own simulator, sets its own throwaway
credentials, and stands up its own local OTLP collector. You'll see one drafted
reply per routine ticket, a `BLOCKED by policy — PIIDetected` line for the
PII-laden one, the remaining budget, and the count of spans exported.

## The governed core

The whole demo is ordinary framework code; the SDK touches it in exactly two
places. First, the model is built off a shared `Donkey` so it rides that
instance's governed transport:

```python
model = donkey.langgraph.chat_model("gpt-4o")
```

Second, the node that calls the model wraps the call in `typed_refusals()`, so a
proxy rejection comes back as a `DonkeyError` subclass instead of a
framework-wrapped generic error:

```python
from donkey_kit.integrations.langgraph import typed_refusals

async def _call_model(state):
    with typed_refusals():
        reply = await model.ainvoke(state["messages"])
    return {"messages": [reply]}
```

The caller binds a run id per ticket and catches the typed refusal:

```python
async with donkey.run(id=f"ticket-{i}"):
    try:
        result = await agent.run(f"Draft a one-line support reply to: {ticket}")
    except PIIDetected as refusal:
        print(f"  ticket {i}: BLOCKED by policy — PIIDetected ({refusal.policy})")
```

  **Honest note on the block.** The simulator triggers on the request *count*
  (`every=5`), not by scanning content — it replays a captured fixture, it is
  not a PII detector. The real gateway does the detection; here the SSN in
  ticket five just makes the blocked ticket read true. See
  [It replays; it does not evaluate](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md#it-replays-it-does-not-evaluate).

## Verification status

The proxy *contract* the demo depends on — the base URL shape (no `/v1`), the
`client_id`/`client_secret` header pair, the attribution headers, and the four
live-verified rejection shapes including PII — is **live-verified**
([Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)). `ChatOpenAI` / `StateGraph` are
the frameworks' own classes and `.ainvoke` is their documented API:
construction via the SDK factory is the verified surface, and everything after
is the framework's own runtime.

## Where to go next

- [Nightly batch](https://donkey-development-kit.github.io/donkey-development-kit/scenarios/nightly-batch.md) — the same governance, applied to an
  unattended overnight job that paces itself against a budget window.
- [Typed refusals](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) — the full exception taxonomy the block above lands
  in, with a per-exception "retryable?" cookbook.
- [Testing & conformance](https://donkey-development-kit.github.io/donkey-development-kit/testing.md) — run this same agent factory through the
  conformance suite (`pytest --donkey-conformance`).

# Support triage with the OpenAI Agents SDK

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

The [support-triage](https://docs.donkey-kit.dev/scenarios/support-triage.md) job, run on the second deep,
conformance-gated adapter (`BG §1.8`, promoted by Phase 1 demand in #223): an
`agents.Agent` drafts a one-line reply to each of five support tickets, and the
fifth, which carries a customer's SSN, comes back as a **typed refusal**. It is
the same five tickets, the same simulator scenario and the same assertions as
the LangGraph demo, so you can compare the two frameworks line for line.

`pip install` to a drafted reply against the [local simulator](https://docs.donkey-kit.dev/simulator.md) only
— no Anypoint credentials, no real gateway.

## What it demonstrates

- **Runs with no gateway.** The demo boots the [local simulator](https://docs.donkey-kit.dev/simulator.md)
  in-process on an ephemeral port and points the SDK at it; the simulator
  ignores auth, so the credentials are throwaway placeholders.
- **The PII-masking branch actually executes.** The simulator runs the
  `pii_block:every=5` [scenario](https://docs.donkey-kit.dev/simulator.md#scenario-scripting): every fifth
  `POST /chat/completions` (the route `OpenAIChatCompletionsModel` calls) is
  served the captured `pii-detected` **403**. The fifth ticket is blocked, and
  the refusal surfaces out of `Runner.run` as a typed [`PIIDetected`](https://docs.donkey-kit.dev/errors.md),
  not the OpenAI client's generic `PermissionDeniedError`.
- **Correlation reaches the agent for free.** Each ticket runs inside a
  [`donkey.run(id=...)`](https://docs.donkey-kit.dev/telemetry.md) block. The agent's dynamic `instructions`
  log the run id without it being passed through the run context, because
  `Runner.run` resolves instructions and runs `function_tool`s inside the
  caller's context. Every model request carries the id on the wire too.
- **Every governed call emits an OTel span**, refusals included, exported to a
  bundled local OTLP collector with [zero config](https://docs.donkey-kit.dev/telemetry.md). The Agents SDK's
  own tracing is switched off: its default exporter posts to OpenAI, not to your
  proxy.

## Run it

### Install the extras

```bash
pip install "donkey-kit[llm,openai-agents,local,otel]"
```

`openai-agents` brings the framework and the adapter, `local` the simulator,
`otel` the OpenTelemetry export path.

### Run the demo

From a checkout of the SDK's `python/` directory:

```bash
python -m examples.openai_agents.main
```

No environment setup: the demo boots its own simulator, sets its own throwaway
credentials, and stands up its own local OTLP collector. You'll see one drafted
reply per routine ticket, a `BLOCKED by policy — PIIDetected` line for the
PII-laden one, the remaining budget, and the count of spans exported.

## The governed core

The agent is ordinary Agents SDK code; the SDK touches it in exactly two places.
First, the model is built off a shared `Donkey`, so it rides that instance's
governed transport:

```python
from agents import Agent, RunConfig, Runner

agent = Agent(
    name="triage",
    instructions=_instructions,
    model=donkey.openai_agents.model("gpt-4o"),
)
```

Second, the call to `Runner.run` is wrapped in
[`typed_refusals()`](https://docs.donkey-kit.dev/errors.md), so a proxy rejection comes back as a
`DonkeyError` subclass instead of the OpenAI client's status error:

```python
from donkey_kit import typed_refusals

async def run(self, text):
    async with typed_refusals():
        return await Runner.run(self._agent, text, run_config=RunConfig(tracing_disabled=True))
```

The caller binds a run id per ticket and catches the typed refusal:

```python
async with donkey.run(id=f"ticket-{i}"):
    try:
        result = await agent.run(f"Draft a one-line support reply to: {ticket}")
    except PIIDetected as refusal:
        print(f"  ticket {i}: BLOCKED by policy — PIIDetected ({refusal.policy})")
```

## Run it through conformance

The demo's `build(donkey)` factory is exactly the shape the customer-facing
[conformance plugin](https://docs.donkey-kit.dev/testing.md) drives, so the same agent can be held to the
four public scenarios:

```bash
pytest --donkey-conformance --donkey-agent=examples.openai_agents.main:build
```

CI runs both the demo and this conformance pass on every pull request (the
`openai-agents-demo` job), and the nightly matrix re-runs them against the
newest Agents SDK release.

  **One asserted exemption: approval round trip.** Under `Runner.run`, the
  Agents SDK turns an exception raised inside a `function_tool` into a
  tool-result string for the model, so an `@donkey.tool(approval="required")`
  gate never pauses the run. It is a documented, test-pinned exemption, not a
  silent skip; mapping DDK approvals onto the SDK's own `needs_approval`
  interruptions is tracked in #213. Every other conformance scenario passes.

  **Honest note on the block.** The simulator triggers on the request *count*
  (`every=5`), not by scanning content — it replays a captured fixture, it is
  not a PII detector. The real gateway does the detection; here the SSN in
  ticket five just makes the blocked ticket read true. See
  [It replays; it does not evaluate](https://docs.donkey-kit.dev/simulator.md#how-the-simulator-works).

## Verification status

The proxy *contract* the demo depends on — the base URL shape (no `/v1`), the
`client_id`/`client_secret` header pair, the `/chat/completions` route and the
live-verified rejection shapes including PII — is **live-verified**, and so is
the `OpenAIChatCompletionsModel` constructor the factory calls (see the
[verification ledger](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md)).
`Agent` / `Runner` are the framework's own classes and `Runner.run` is its
documented API: construction via the SDK factory is the verified surface, and
everything after is the framework's own runtime.

## Where to go next

- [Support triage on LangGraph](https://docs.donkey-kit.dev/scenarios/support-triage.md) — the same job on
  the first deep adapter.
- [OpenAI Agents SDK examples](https://docs.donkey-kit.dev/examples/openai-agents.md) — a tool-calling
  `Runner`, simulated refusals, and the `/responses` route via
  `connection_kwargs()`.
- [Typed refusals](https://docs.donkey-kit.dev/errors.md) — the full exception taxonomy the block above lands
  in.

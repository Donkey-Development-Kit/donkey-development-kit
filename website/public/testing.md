# Testing & conformance

Live

Three test-time tools, all serving the same captured gateway rejections:

- **`simulate()`** — make the next N calls through a `Donkey` return a chosen
  refusal, in process, with no server.
- **The conformance plugin** — a pytest suite you point at *your own* agent to
  find governance bugs you did not know you had.
- **The `gateway` fixture** — a real simulator on an ephemeral port, for
  subjects that do not import the SDK, with a spy on what it received.

```bash
pip install "donkey-kit[test]"          # simulate() + conformance plugin
pip install "donkey-kit[test,local]"    # add the gateway fixture
```

## `simulate()` — in-process, no server

A context manager that makes the next N calls through a `Donkey` return a
chosen refusal. It injects the **same captured rejection fixture** the error
classifier and the [local simulator](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md) use, so your agent sees exactly
that typed refusal — not a hand-rolled stand-in. No network, no server, fast
enough for unit tests:

```python
from donkey_kit import Donkey, PIIDetected

async def test_agent_masks_pii():
    donkey = Donkey.from_env()
    with donkey.simulate(PIIDetected):
        result = await triage_agent.run(ticket_with_card_number)
    assert "****" in result.draft_reply
```

- `times=N` (default `1`) sets how many calls are refused before requests
  proceed normally. Retries of a single logical call count once.
- The previous transport is restored on exit, even if the block raises, and
  nested `simulate()` blocks compose.
- Every injected response carries `x-donkey-simulator: true`, so a simulated
  refusal is never mistaken for a real gateway response in a log or trace.

The selectable refusals are the ones produced from a captured fixture:
`TokenBudgetExceeded`, `PIIDetected`, `PromptInjectionBlocked`,
`ContentSafetyBlocked`, `UpstreamRequestError`, `UpstreamModelError`,
`AuthError`, and the generic `PolicyViolation` (the content-moderation shape).
Asking for a refusal no gateway response produces — for example a client-side
`ConfigError` — raises a `ValueError`.

  `simulate()` injects the fixture verbatim. For scripted behaviour such as
  every-Nth-call PII blocks or a shrinking budget window, use the
  [`gateway` fixture](#the-gateway-fixture) with
  `set_scenarios(...)`, or [`donkey mock --scenario`](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md#scenario-scripting).

## The conformance plugin — run it against your agent

Installing `donkey-kit[test]` registers a pytest plugin with a conformance suite
you point at your own agent factory:

```bash
pytest --donkey-conformance --agent=my_app.agent:build
```

It answers questions you probably cannot currently answer about your own code:

- Does your agent **retry** a `TokenBudgetExceeded`? *It must not* — a policy
  refusal is terminal, and retrying it burns budget to earn the same refusal.
- Does it swallow `PIIDetected` as a generic exception?
- Does it propagate the correlation ID into its own logs?
- Does it still work when budget headers are absent entirely?

Each scenario becomes one pytest item, and the run prints a table of
scenario → pass / fail / **exempt**. A failure reads as a finding about your
agent — for example, *"your agent retried a budget refusal 3 times"* — found in
CI, before production.

The factory is called once per scenario and receives the `donkey` fixture if it
declares one. With `--donkey-conformance`, the plugin runs the suite
exclusively in place of normal test collection; without the flag it is inert.
You can also run it through the CLI with `donkey test --agent my_app.agent:build`
(see [CLI](https://donkey-development-kit.github.io/donkey-development-kit/cli.md#donkey-test)).

### Exemptions

  **Exemptions are asserted, never silently skipped.** If your agent
  legitimately cannot satisfy a scenario, record it in a `KNOWN_LIMITATIONS`
  mapping of `{scenario: reason}` as an explicit, reviewable claim.

By default the plugin reads a `KNOWN_LIMITATIONS` attribute from the `--agent`
module; point elsewhere with `--donkey-known-limitations=module:NAME`. The
mapping is validated at collection time, so an unknown scenario key or an empty
reason fails the run before any scenario executes.

## The `gateway` fixture

`simulate()` and the conformance plugin run your subject **in process**. Some
subjects cannot be: a containerised agent, a Node service, an A2A client, a
manual `curl`. Those need a real listener on a real port — and often you need
to know *what the gateway actually received*. "Did my agent stop after the
refusal, or retry four more times?" is answerable only from the gateway's side.

The `gateway` fixture boots the [local simulator](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md) on an ephemeral
port, hands your test its `.url`, and records every request:

```python
async def test_agent_stops_after_a_refusal(gateway):
    gateway.set_scenarios("pii_block:every=1")
    app = deploy(env={"DONKEY_LLM_PROXY_URL": gateway.url})
    await app.run(ticket_with_card_number)
    # A policy refusal is terminal — the agent must not retry it.
    assert gateway.requests_received == 1
```

- `gateway.url` is a real `http://127.0.0.1:<port>` any process can point
  `DONKEY_LLM_PROXY_URL` at.
- The port is bound to `0`, so parallel `pytest -n` runs never collide.
- `gateway.requests_received` counts requests; `gateway.requests` exposes each
  one's method, path, and headers, with `client_secret` **redacted**.
- `gateway.set_scenarios(...)` arms [scenario scripting](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md#scenario-scripting)
  (`pii_block`, `injection`, `budget`) **per test**.
- Every response carries `x-donkey-simulator: true`.
- The server is torn down when the test exits, including on failure.

The fixture needs the `[local]` extra (Starlette + Uvicorn); requesting it
without that extra raises an `ImportError` naming the exact `pip install`.

## Choosing a tool

| | [`donkey mock`](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md) | `gateway` fixture | `simulate()` |
|---|---|---|---|
| Shape | A **server** you run | A **server** a test starts | **In-process** context manager |
| Port | You pick it (`--port`) | Ephemeral (`0`), on `.url` | None |
| Use it for | Manual dev, demos, any client | Testing an out-of-process subject | Fast unit tests |
| Needs a network | Yes (localhost) | Yes (localhost) | No |
| Works with a stock OpenAI client | Yes | Yes | No — it hooks the `Donkey` transport |
| Asserts on what the gateway received | No | Yes (`requests_received`) | No |

All three read the **same rejection fixtures**, so they cannot drift apart.

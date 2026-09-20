# Testing & conformance

  **Phase 1 — all three ship.** In-process refusal injection
  (`donkey.simulate()`), the pytest conformance plugin
  ([#191](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/191)),
  and an out-of-process `gateway` fixture
  ([#278](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/278))
  are all shipped. The conformance plugin runs your agent **in process** through
  its own `Donkey`; the `gateway` fixture serves the simulator on a **real port**
  for a subject that does not import the SDK. See [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md) and
  [Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md).

Three test-time tools, and the second one is the most important of the set.

## `simulate()` — in-process, no server

A context manager that makes the next N calls through a `Donkey` return a
chosen refusal — the **same captured rejection fixture** `classify()` and the
[local simulator](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md) are tested against, so it lights up as exactly
that typed refusal, not a hand-rolled stand-in. No network, no server, fast
enough for unit tests:

```python
from donkey_kit import Donkey, PIIDetected

async def test_agent_masks_pii():
    donkey = Donkey.from_env()
    with donkey.simulate(PIIDetected):
        result = await triage_agent.run(ticket_with_card_number)
    assert "****" in result.draft_reply
```

`times=N` (default `1`) sets how many calls are refused before requests proceed
normally; retries of a single logical call count once, and the previous
transport is restored on exit even if the block raises (nesting composes).
Every injected response carries `x-donkey-simulator: true`, so a simulated
refusal is never mistaken for a real gateway response in a log or trace.

The selectable refusals are the ones `classify()` produces from a captured
fixture: `TokenBudgetExceeded`, `PIIDetected`, `PromptInjectionBlocked`,
`ContentSafetyBlocked`, `UpstreamRequestError`, `UpstreamModelError`, `AuthError`,
and the generic `PolicyViolation` (the still-under-documented content-moderation
shape, [#253](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/253)).
Asking for a refusal with no captured fixture — e.g. a client-side
`ConfigError`, which no gateway response produces — is a clear `ValueError`,
never a silent miss.

  `simulate()` injects the fixture **verbatim**. Shaping the body — specific
  PII entity types, a custom message, a shrinking budget window — is the
  follow-up [#188](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/188),
  shared with the simulator's scenario scripting.

## The conformance plugin — run it against *your* agent

This is the part that matters. `pip install "donkey-kit[test]"` publishes a
pytest plugin exposing a `donkey` fixture and a conformance suite you point at
your own agent:

```bash
pytest --donkey-conformance --agent=my_app.agent:build
```

It asks questions you probably cannot currently answer about your own code:

- Does your agent **retry** a `TokenBudgetExceeded`? *It must not* — a policy
  refusal is terminal, and retrying it burns budget to earn the same refusal.
- Does it swallow `PIIDetected` as a generic exception?
- Does it propagate the correlation ID into its own logs?
- Does it still work when budget headers are absent entirely?

The output is a table of scenario → pass / fail / **exempt**.

## The `gateway` fixture — an out-of-process server tests assert against

`simulate()` and the conformance plugin both run your subject **in process**.
Some subjects cannot be: a containerised agent, a Node service, an A2A client, a
manual `curl` — none of which import `donkey_kit`. Those need a real listener on a
real port, and they need the other half of the question `simulate()` cannot
answer: *what did the gateway actually receive?* "Did my agent stop after the
refusal, or retry four more times?" is answerable only from the gateway's side.

The `gateway` fixture (shipped in `donkey-kit[test]`, beside `donkey`) boots the
[local simulator](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md) on an **ephemeral port**, hands your test its
`.url`, and records every request in a spy you can assert on:

```python
async def test_agent_stops_after_a_refusal(gateway):
    gateway.set_scenarios("pii_block:every=1")
    app = deploy(env={"DONKEY_LLM_PROXY_URL": gateway.url})
    await app.run(ticket_with_card_number)
    # A policy refusal is terminal — the agent must not retry it.
    assert gateway.requests_received == 1
```

- `gateway.url` is a real `http://127.0.0.1:<port>` any process can point
  `DONKEY_LLM_PROXY_URL` at — it does not import the SDK.
- The port is bound to `0`, so parallel `pytest -n` runs never collide.
- `gateway.requests_received` counts requests; `gateway.requests` exposes each
  one's method, path and headers, with `client_secret` **redacted**.
- `gateway.set_scenarios(...)` arms the same
  [scenario scripting](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md#scenario-scripting) (`pii_block`, `injection`,
  `budget`) **per test**, not just per process.
- Every response carries `x-donkey-simulator: true`, so a fixture reply is never
  mistaken for a real gateway.
- The server is torn down when the test exits, including on failure.

It serves the **same captured rejection fixtures** as `simulate()` and the
conformance plugin, so the three cannot drift apart. Because it starts a real
server, it needs the `[local]` extra (`starlette` + `uvicorn`); taking the
fixture without it raises an `ImportError` naming the exact `pip install`.

## Why this is the moment the SDK earns trust

A team installs the SDK and on day one gets a failing test that says *"your
agent retried a budget refusal 3 times."*

That is a real bug they did not know they had, found in CI, phrased in a way
nobody argues with. It is worth more than any feature list, because it is the
SDK telling the team something true about their own code rather than asking
them to take a claim on faith.

  **Exemptions are asserted, never silently skipped.** If your agent
  legitimately cannot satisfy a scenario, it is recorded in
  `KNOWN_LIMITATIONS` as an explicit, reviewable claim. A quiet
  `pytest.skip` would turn the suite into decoration — the same rule this
  repo already applies to its own adapters.

## How this differs from the simulator

They all exist, they share fixtures, and they are not the same tool:

| | [`donkey mock`](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md) | `gateway` fixture | `simulate()` |
|---|---|---|---|
| Shape | A **server** you run | A **server** a test starts | **In-process** context manager |
| Port | You pick it (`--port`) | Ephemeral (`0`), on `.url` | None |
| Use it for | Manual dev, demos, any client | Testing an out-of-process subject | Fast unit tests |
| Needs a network | Yes (localhost) | Yes (localhost) | No |
| Works with a stock OpenAI client | Yes | Yes | No — it hooks the `Donkey` transport |
| Asserts on what the gateway received | No | Yes (`requests_received`) | No |

They read the **same rejection fixtures**, so they cannot drift apart.

## Acceptance bar

- `simulate()` works with no network and no server running. ✅
- `times=N` is honoured exactly; call N+1 proceeds normally, and nesting or an
  exception restores the previous transport. ✅
- It replays the *same fixtures* `classify()` and the simulator are tested
  against, so the three fail together if the gateway contract ever drifts. ✅
- The plugin registers as a pytest entry point, and
  `pytest --donkey-conformance --agent=my_app.agent:build` turns each scenario
  into one pytest item and prints a scenario table. ✅
- Exemptions must be asserted in code — a `KNOWN_LIMITATIONS` mapping next to
  your factory (or `--donkey-known-limitations=module:NAME`), validated at
  collection time so a bad key or empty reason fails the run loudly; a silent
  skip is never possible. ✅
- The `gateway` fixture binds an ephemeral port, exposes `.url` to a process that
  does not import `donkey_kit`, records requests with `client_secret` redacted,
  takes scenarios per test, and tears down on exit. ✅

---

**Status: Phase 1 — `simulate()`, the conformance plugin
([#191](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/191)),
and the `gateway` fixture
([#278](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/278))
are all shipped.**

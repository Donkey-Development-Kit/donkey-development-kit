# Local simulator

  **Phase 1 — the replay server ships.** `donkey mock` boots a local
  server that replays the captured rejection fixtures, honesty-stamps every
  response, and synthesises a budget window on the happy path. Configurable
  *scenario scripting* (frequency-based failures, a shrinking budget window) is
  the linked follow-up, [#188](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/188).
  See [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md) and [Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md).

Nobody can make the production gateway emit a PII block on cue. Which means
the branch of your agent that handles `PIIDetected` has **never executed**
before the day it executes on a real customer's data.

`donkey mock` is a local HTTP server that behaves like the governed proxy
*for the failure paths*. It replays captured rejection fixtures on demand and
replays a captured happy-path completion for everything else.

```bash
pip install "donkey-kit[local]"
donkey mock --port 8080        # --host defaults to 127.0.0.1
```

Then point **any** client at `http://localhost:8080` — the SDK, a stock OpenAI
client, or cURL. To trigger a specific rejection, set the request's **`model`**
to the sentinel `donkey-sim/<shape>`:

```bash
# note: no /v1 segment — the governed proxy (and the simulator) has none
curl -s http://localhost:8080/responses \
  -H 'content-type: application/json' \
  -d '{"model": "donkey-sim/pii-detected"}' -i | head -1
# HTTP/1.1 403 Forbidden      (byte-identical to the captured PII block)
```

The selectable shapes are `token-rate-limit`, `pii-detected`,
`injection-protection`, `regex-prompt-guard`, `content-safety`,
`content-moderation`, `model-not-found`, `upstream-5xx`, and `client-id-missing`
— the eight documented rejections plus the consumer-auth `401`. Any other
`model` value gets the happy path. The `donkey-sim/` prefix is
a simulator-only control surface; the real gateway never interprets it.

## Why this is the one feature `base_url` cannot give you

Everything else the SDK does happens on the client side, so a determined
developer could hand-roll it. This cannot be hand-rolled, because **the value
is on the server side.** "Develop against a governed gateway without having a
gateway" is not an ergonomics improvement; it is a capability you otherwise do
not have.

## Testing the branch that has never run

Point your agent at the simulator and send `model: "donkey-sim/pii-detected"`
on the calls you want to fail. Your masking logic runs in your own terminal,
before the pull request is opened, instead of in production — and because the
body is the *same fixture* `classify()` is tested against, it lights up as a
typed `PIIDetected`, not a raw `403`.

The happy path carries a synthesised `x-token-*` budget window that decrements
on every call, so the pacing and resume logic from [Budget & pacing](https://donkey-development-kit.github.io/donkey-development-kit/budget.md)
runs end-to-end against the simulator instead of "we'll find out tonight."

  Deciding *which* calls fail by hand (send the sentinel) is what ships today.
  Declarative scenario scripting — "fail every fifth call", "shrink the budget
  window to 60s" — is the follow-up
  [#188](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/188).

## The honesty guarantee

A simulator that could be mistaken for a real gateway would be worse than no
simulator, so two rules are non-negotiable:

  **Every simulator response carries `x-donkey-simulator: true`.** It can
  never be confused for a real gateway in a log, a trace, or a screenshot.

And the fixtures are the **same files** used by the `classify()` tests. One
source of truth: if the gateway contract drifts, the simulator and the error
taxonomy fail together, rather than the simulator quietly teaching you a
contract that no longer exists.

## Acceptance bar

- A **stock non-SDK client** (plain `httpx`, not `donkey.llm.client()`) receives
  byte-identical rejection bodies and the exact discriminator headers — proof
  the simulator is honest rather than merely convenient. Formalising the same
  against a stock `openai.OpenAI(base_url="http://localhost:8080", …)` client is
  [#189](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/189). ✅
- The fixtures are the *same files* the `classify()` contract tests load, so
  the two fail together if the gateway contract ever drifts. ✅
- Every response carries `x-donkey-simulator: true`. ✅
- Ships as `pip install "donkey-kit[local]"`, pure-Python, no Docker. ✅
- Happy-path responses emit a synthesised `x-token-*` window so budget pacing
  works against it — an **UNVERIFIED overlay** (the live `200` capture carries
  no budget headers; whether the real proxy emits them is pending
  [#253](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/253),
  recorded in [`docs/verified-apis.md`](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md)).
- Declarative scenario scripting with worked examples —
  [#188](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/188).

---

**Status: Phase 1 — the fixture-replay server (`donkey mock`) is shipped;
scenario scripting is [#188](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/188).**

# Local simulator

Live

Nobody can make the production gateway emit a PII block on cue, so the branch
of your agent that handles `PIIDetected` usually runs for the first time on a
real customer's data.

`donkey mock` fixes that. It is a local HTTP server that behaves like the
governed LLM proxy *for the failure paths*: it replays captured rejection
fixtures on demand and a captured happy-path completion for everything else.

```bash
pip install "donkey-kit[cli,local]"
donkey mock --port 8080        # --host defaults to 127.0.0.1
```

Point **any** client at `http://localhost:8080` — the SDK, a stock OpenAI
client, or cURL. To trigger a specific rejection, set the request's **`model`**
to the sentinel `donkey-sim/<shape>`:

```bash
# no /v1 segment — the governed proxy (and the simulator) has none
curl -s http://localhost:8080/responses \
  -H 'content-type: application/json' \
  -d '{"model": "donkey-sim/pii-detected"}' -i | head -1
# HTTP/1.1 403 Forbidden      (byte-identical to the captured PII block)
```

The selectable shapes are `token-rate-limit`, `pii-detected`,
`injection-protection`, `regex-prompt-guard`, `content-safety`,
`content-moderation`, `model-not-found`, `upstream-5xx`, and `client-id-missing`
— the eight documented rejections plus the consumer-auth `401`. One happy-path
variant is selectable the same way: `donkey-sim/success-semantic` replays the
captured **semantic-routing** `200` (`routing_type == "Semantic"`), so
`donkey.last_call.matched_topic` and `routing_score` light up offline (see
[Gateway identity](https://donkey-development-kit.github.io/donkey-development-kit/examples/gateway-identity.md#semantic-routing-the-matched-topic-and-score)).
Any other `model` value gets the default model-based happy path. The
`donkey-sim/` prefix is a simulator-only control surface; the real gateway never
interprets it.

Because each body is the *same fixture* the SDK's error classifier is tested
against, a sentinel request surfaces in your agent as the typed exception — a
[`PIIDetected`](https://donkey-development-kit.github.io/donkey-development-kit/errors.md), not a raw `403`.

## Budget windows on the happy path

Happy-path `200` responses carry a synthesised `x-llm-proxy-ratelimit` budget
window that decrements on every call, so the pacing logic from
[Budget & pacing](https://donkey-development-kit.github.io/donkey-development-kit/budget.md) runs end-to-end against the simulator. For a real
windowed counter that resets and emits the `429` on exhaustion, use the
[`budget` scenario](#scenario-scripting).

## Scenario scripting

The sentinel lets *you* decide which call fails. A **scenario** lets the
simulator decide, on a rule you set once at boot, so failures fire on their own
and deterministically across a whole run. Pass `--scenario` once per rule; the
grammar is `<name>:<key=value,key=value>`.

```bash
donkey mock --port 8080 \
  --scenario pii_block:every=5 \
  --scenario budget:limit=20000,window=60s \
  --scenario injection:on-pattern="ignore previous"
```

When more than one is set, they are evaluated `injection` → `pii_block` →
`budget`, and the first that fires wins. The `donkey-sim/<shape>` sentinel
takes precedence over all of them — it is an explicit "force this exact shape"
override.

  The sentinel is for one-off failures; a scenario runs across a whole session.
  Use both together to cover specific calls and background failure rates.

### `pii_block:every=N`

Serves the captured `pii-detected` **403** on every Nth `POST /responses`; the
other calls get the happy path.

```bash
donkey mock --scenario pii_block:every=5
```

Every fifth call raises a typed [`PIIDetected`](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) in your agent, so your
masking logic runs in your own terminal before the pull request is opened.

### `budget:limit=<tokens>,window=<duration>`

Runs a **real, wall-clock-windowed token counter**. Shrink an hour-long window
to a minute and your [pacing and resume logic](https://donkey-development-kit.github.io/donkey-development-kit/budget.md) is exercised in ninety
seconds.

```bash
donkey mock --scenario budget:limit=20000,window=60s
```

Each call deducts the happy-path completion's reported token usage (override
with `cost=<tokens>`). While budget remains, the `200` carries the
`x-llm-proxy-ratelimit` prose header, decreasing as you spend. Once the window
is spent, calls receive the captured `token-rate-limit` **429**, with
`x-token-remaining` and `x-token-reset` recomputed from the counter and the
milliseconds left in the window, until the window rolls over. `duration`
accepts `ms`, `s`, or `m` (a bare number is seconds).

  The numeric `x-token-*` headers appear only on the `429`, and the prose
  `x-llm-proxy-ratelimit` header only on the `200` — matching the real gateway.
  The simulator never emits a header shape the gateway does not.

### `injection:on-pattern=<substring>`

Serves the `injection-protection` **400** on any request whose `input`,
`messages`, or `instructions` text contains the substring (case-insensitive).

```bash
donkey mock --scenario injection:on-pattern="ignore previous"
```

The request surfaces as a typed [`PromptInjectionBlocked`](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) (via the
`x-injection-protection: blocked` discriminator), so the refusal branch of a
guardrailed bot is exercised before it faces a real attack.

## How the simulator works

  **Every simulator response carries `x-donkey-simulator: true`** — including
  framework-generated `405`/`500` responses — so a simulated response is never
  confused with a real gateway in a log, a trace, or a screenshot.

- **It replays; it does not evaluate policy.** The simulator tests how your
  agent handles a refusal, never *which* prompts get refused. You choose the
  refusal (the `donkey-sim/<shape>` sentinel or a `--scenario` rule); the
  simulator never inspects a prompt and decides it violates a policy, and it
  ignores authentication. Testing whether a prompt would be blocked by your
  deployed policy configuration requires the real gateway.
- **It works with any client.** A stock, non-SDK client — plain `httpx` or
  `openai.OpenAI(base_url="http://localhost:8080", …)` — receives the
  byte-identical rejection bodies and exact discriminator headers.
- **Its fixtures are the SDK's test fixtures.** The simulator serves the same
  files the error classifier is tested against, so the simulator and the
  [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) cannot drift apart. The wheel ships a sha256
  integrity manifest of every fixture, so a changed byte fails loudly rather
  than silently altering what the simulator replays.
- **It is pure Python.** `pip install "donkey-kit[local]"` adds Starlette and
  Uvicorn; no Docker required.

## Related

- [Testing & conformance](https://donkey-development-kit.github.io/donkey-development-kit/testing.md) — `simulate()` for in-process unit tests,
  and the `gateway` pytest fixture that runs this simulator on an ephemeral port.
- [Error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) — the typed exceptions each shape maps to.
- [CLI](https://donkey-development-kit.github.io/donkey-development-kit/cli.md) — the full `donkey` command reference.

# Internal copilot

An internal copilot answers employee questions against company systems. Its
output has to clear a **content-safety guardrail** before it reaches a person,
and when the guardrail fires you need the refusal to (a) surface as something
you can branch on and (b) carry an id that joins the block back to the run in
your own logs. This page runs that content-safety branch against the [local
simulator](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md), and is honest about the parts of a full internal
copilot that are still [blocked on verification](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md).

## What it demonstrates

- **The content-safety branch executes** as a typed
  [`ContentSafetyBlocked`](https://donkey-development-kit.github.io/donkey-development-kit/errors.md), driven by the `donkey-sim/content-safety`
  sentinel — no need to craft a prompt that a real guardrail would reject.
- **Per-run correlation joins the refusal to your logs.** Inside a
  [`donkey.run(id=...)`](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md) block the bound id becomes the
  `X-Correlation-Id` on every request *and* lands on
  `ContentSafetyBlocked.correlation_id`, so a log line for the block joins to
  the gateway's own record with no extra wiring.

## Run it

### Install the extras

```bash
pip install "donkey-kit[llm,local]"
```

### Boot the simulator

```bash
donkey mock --port 8080
```

No `--scenario` needed: the [`donkey-sim/<shape>` sentinel](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md) lets
*you* pick which call fails by setting the request's `model`. The selectable
shapes include `content-safety`.

### Force a content-safety block and catch it typed

```python
import asyncio
import openai
from donkey_kit import Donkey, ContentSafetyBlocked
from donkey_kit.core.errors import classify

async def ask(donkey, question, *, run_id):
    client = donkey.llm.client()          # the native AsyncOpenAI, governed transport
    async with donkey.run(id=run_id):
        try:
            return await client.chat.completions.create(
                # the sentinel forces the captured content-safety 403 from the simulator
                model="donkey-sim/content-safety",
                messages=[{"role": "user", "content": question}],
            )
        except openai.APIStatusError as e:
            governed = classify(e.response)       # -> a DonkeyError subclass
            if isinstance(governed, ContentSafetyBlocked):
                print(f"[{run_id}] blocked by guardrail:", governed.remediation)
                print(f"[{run_id}] correlation id:", governed.correlation_id)
            raise governed from e

asyncio.run(...)   # DONKEY_LLM_PROXY_URL=http://localhost:8080, throwaway creds
```

  **The raw client raises `openai.APIStatusError`, not a `DonkeyError`.**
  `donkey.llm.client()` is the real OpenAI SDK, so you bridge into the taxonomy
  with `classify(e.response)` — see [Bridging from the raw
  client](https://donkey-development-kit.github.io/donkey-development-kit/errors.md#bridging-from-the-raw-client). An adapter that wraps calls in
  `typed_refusals()` (as the [support-triage](https://donkey-development-kit.github.io/donkey-development-kit/scenarios/support-triage.md) demo
  does) surfaces the typed refusal directly instead.

## What a full internal copilot also needs — and what's blocked

A production internal copilot wants more than a content-safety branch. Three of
those pieces are **not yet buildable** because they depend on surfaces still
blocked on verification — this page shows the *shape* without inventing
an endpoint:

  **Governed access to internal tools** — reaching company systems through
  governed MCP tools rather than ad-hoc HTTP — is a [Phase 2 surface](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md).
  Exchange→MCP tool discovery is still blocked on verification; the SDK raises
  `NotImplementedError("blocked on verification: …")` at call time rather than
  guessing an endpoint. See [Tool access](https://donkey-development-kit.github.io/donkey-development-kit/tool-access.md).

  **Agent identity and a kill switch** — a verifiable identity for the copilot,
  and the ability to disable it centrally — are platform capabilities the SDK's
  job is to make *reachable and typed*, not to reimplement. Both are
  [Phase 2](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md) and gated on verifying the platform's own contract; see
  [Identity](https://donkey-development-kit.github.io/donkey-development-kit/identity.md). Until then the SDK does not fabricate a stand-in.

## Verification status

The content-safety **discriminator** (the vendor `…-action: reject` header) is
typed by `donkey_kit.core.errors.classify()`, but its exact body is
**documented-but-not-live-captured** — pinned from the policy pages and pending
a live sandbox round-trip ([Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)). The
simulator replays the captured fixture so the branch runs today; no verification
row flips to `verified` until a live capture confirms the shape. The correlation
mechanism (`donkey.run()` → `X-Correlation-Id` → `.correlation_id`) is shipped
and framework-agnostic.

## Where to go next

- [Typed refusals](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) — the `ContentSafetyBlocked` shape and the
  documented-but-not-captured caveat, plus the retryable cookbook.
- [Telemetry & cost](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md) — how the run id ties spans, logs, and the
  gateway record together.
- [Support triage](https://donkey-development-kit.github.io/donkey-development-kit/scenarios/support-triage.md) — the same governance surfacing a
  refusal directly through a framework adapter.

# Budget & pacing

Live

Once a token-rate-limit policy is applied, the governed proxy reports your
token budget on its responses, in **two shapes** depending on the response:

| Response | Header | Example |
|---|---|---|
| Success `200` (and a `403` refusal) | `x-llm-proxy-ratelimit`, as prose | `Token rate limit: 10000 tokens remaining of 10000 limit. Reset in 56711ms.` |
| Budget refusal `429` | `x-token-limit`, `x-token-remaining`, `x-token-reset` | numeric values |

Parsing those yourself in every call site is tedious, and most code skips
it — so the first time budget matters is the moment it runs out.

With DDK **you never parse a header.** Every response that carries either shape
updates a `Budget` object on the `Donkey` instance. Where both are present the
numeric values win, and the prose header fills any field they leave unset.
Until the first such response, every field is `None`, never a misleading zero.

## The object

```python
donkey.budget.limit          # int, tokens per window
donkey.budget.remaining      # int, from the last response
donkey.budget.reset_at       # datetime, converted from the ms-to-reset value — not raw
donkey.budget.observed_at    # when headers were last seen (staleness)
donkey.budget.fraction_used  # 0.0–1.0
```

And two helpers that use it:

```python
await donkey.budget.wait_for_reset()          # sleeps until reset_at

async with donkey.budget.pace(reserve=0.10):  # raises BudgetReserveReached at 90%
    ...
```

`pace()` raises **before** issuing the request that would cross your reserve —
not after a `429` comes back.

The budget object is per-`Donkey`, not global: two instances with different
credentials do not share state.

## Example: a batch job that finishes by itself

50,000 product records, enriched overnight against a governed model, budget
window resetting every hour, no human awake.

Without a budget object, the script runs flat out, takes a `429` at record
31,000, crashes, and someone re-runs it from record 0 in the morning —
spending the budget twice to do the same work. With `pace()`:

```python
for batch in chunks(records, 200):
    while True:
        try:
            async with donkey.budget.pace(reserve=0.05):
                await enrich(batch)
        except BudgetReserveReached as exc:
            if exc.reset_at is None:
                raise  # waiting cannot make progress without a reset time
            await donkey.budget.wait_for_reset()
            continue
        break
    checkpoint(batch)
```

Once `reset_at` has elapsed, the old observation is stale and `pace()` no longer
refuses, so the job continues unattended without a manual budget observation. A
later response updates the observed fields only when it carries a recognised
budget signal, and a fresh future `reset_at` makes the guard active again.

If a partial observation reports usage without a `reset_at`, the loop above
re-raises `BudgetReserveReached` after one attempt instead of calling
`wait_for_reset()` and spinning at zero delay. Preserve the last checkpoint and
escalate rather than crossing the reserve.

## Example: a dashboard that prevents the outage

`fraction_used` is per-agent, so it can be graphed. The owner of a support
agent sees it climbing at 14:00 and asks for an increase before the 16:00
peak — rather than explaining an outage afterwards.

## Budget is observed in-band

  The gateway reports budget on response headers; there is **no endpoint that
  answers "what is my remaining budget?"**. So `remaining` is only as fresh as
  your last call, and a brand-new process knows nothing until its first request
  completes.

This is why `observed_at` is part of the public surface: a dashboard reading
`remaining` without checking `observed_at` is reporting history, not state.
A budget-query endpoint on the gateway would make this object live rather than
last-known-good — see [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md).

## Semantic cache steering

Live

When the proxy is fronted by the Anypoint **semantic-caching** policy, the
gateway can answer a request from a stored completion when a semantically
similar prompt was seen before — no provider round-trip, no fresh token spend.
DDK caches nothing and computes no embeddings itself (that stays on the
[do-not-build](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md) list); it lets you **steer** the gateway's cache per
block and **surfaces** the outcome.

```python
# Skip the cache for a block where a fresh answer matters:
with donkey.cache(skip=True):
    await agent.run(task)

# Or tighten the match and shorten entry lifetime:
async with donkey.cache(threshold=0.9, ttl=60):
    ...
```

`donkey.cache(...)` is a dual sync/async context manager — like
[`donkey.run(...)`](https://donkey-development-kit.github.io/donkey-development-kit/identity.md), the controls bind to a context variable, so they
reach every governed call in the block (including calls on framework-spawned
`asyncio` tasks) with no threading through framework state. The five controls:

| Control | Type | Effect |
|---|---|---|
| `skip` | `bool` | Bypass the cache policy entirely (passthrough to the provider). |
| `no_store` | `bool` | Look up, but do not write the result on a miss. |
| `ttl` | `int` | Override the entry time-to-live, in seconds (a positive int). |
| `threshold` | `float` | Override the similarity threshold, in `[0.0, 1.0]`. |
| `principal_id` | `str` | Override the id the similarity filter partitions on. |

An invalid control (a negative `ttl`, a `threshold` outside `[0.0, 1.0]`, a
`principal_id` with a control character) raises `ConfigError` **at the call
site**, not on the first request. The **outcome** of each call is on
[`donkey.last_call.cache_status`](https://donkey-development-kit.github.io/donkey-development-kit/reference/last-call.md#semantic-cache) /
`.cache_score` and the OTel span. The same
[degradation](https://donkey-development-kit.github.io/donkey-development-kit/frameworks.md) as `donkey.run(...)` applies: a `connection_kwargs()`
/ LiteLLM-backed adapter that does not route through the shared transport does
not see the context variable, so its calls are not steered.

## Behaviour guarantees

- The reset value (milliseconds *until* reset, in both `x-token-reset` and the
  prose `Reset in …ms`) is converted to a `datetime` anchored to
  `observed_at`, accurate to the second.
- `pace()` raises before the request that would cross the reserve, never after
  a `429`.
- After `reset_at`, `pace()` no longer refuses, so a wait-and-retry loop can
  continue without a manual budget observation.
- If the reserve is reached without a known `reset_at`, the retry loop raises
  once instead of spinning at zero delay.
- A semantic-cache **hit** is a verbatim replay with no provider round-trip, so
  it never advances the budget window — the replayed `usage` is not fresh spend.
- Budget state is per-`Donkey` instance.

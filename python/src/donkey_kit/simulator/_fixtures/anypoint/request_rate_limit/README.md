# Request rate limiting — LIVE capture (docs/verified-apis.md §4, #974)

Captured **2026-10-07** from the real deployed proxy `ddk-request-rate-limit` in
the DDK sandbox, env **Sandbox** (svc id `00000000-0000-4000-8000-5c4fd1a49fc3`,
from the `x-envoy-decorator-operation` header). API Manager instance
**`21188400`**, deployed to the `private-space-omni-gateway` Flex Gateway, routed
model-based to `azureopenai`/`gpt-5-mini`. The policy is the stock
`rate-limiting` **1.5.1** (Flex implementation `rate-limiting-flex` 1.2.2):
**3 requests per `client_id` per 60000 ms**, `exposeHeaders: true`. Consumer auth
is the `client_id`/`client_secret` pair. UUIDs keep their last 12 hex digits
behind the `00000000-0000-4000-8000-` prefix, as in the sibling captures.

The probe was a hand-written `httpx` script, not `scripts/capture_fixture.py`:
it sent five `POST /chat/completions` calls back to back, then one more after
the window reset. Header names are lowercased (httpx); values are verbatim.

## What is verified (all witnessed live)

- **Every 200 carries the gateway's request window**, unsuffixed:
  `x-ratelimit-limit: 3`, `x-ratelimit-remaining: 2` / `1` / `0`, and
  `x-ratelimit-reset: <ms until the window ends>` (53316, 51522, 49196: a delta
  that counts down with the wall clock). Streaming 200s
  (`text/event-stream`) carry the same three headers.
- **The window is fixed and wall-clock aligned**: both observed windows ended
  on the same second-of-minute, so the first request does not start it. After
  the reset `remaining` is back to `limit - 1`.
- **The 4th and 5th calls → 429** with body `{"error":"Too Many Requests"}`
  (`content-type: application/json; charset=UTF-8`, 29 bytes), the same
  `x-ratelimit-limit` / `x-ratelimit-remaining: 0` / `x-ratelimit-reset` trio,
  the `x-llm-proxy-routing-*` headers and the decorator op. There is **no**
  `retry-after`, **no** `x-token-*`, and no upstream Azure header: the upstream
  was never called.
- **These are not the upstream's headers.** Azure's own passthrough on the 200
  uses suffixed names (`x-ratelimit-limit-requests: 250`,
  `x-ratelimit-remaining-requests`, `x-ratelimit-reset-requests`, the
  `-tokens` variants, `x-ratelimit-renewalperiod-*`, `x-ratelimit-key`). The
  SDK reads only the unsuffixed gateway trio.

What tells this 429 apart from the token-rate-limit 429 (`../llm_proxy/`):
that one has an empty body and the `x-token-limit` / `-remaining` / `-reset`
trio. With `exposeHeaders: false` the request-limit 429 would carry no
`x-ratelimit-*` header; that shape is **not** captured, so `classify()` maps an
unmarked 429 to `TokenBudgetExceeded` as before.

## Files

- `reject.request-rate-limit.headers.txt` / `.body.json`: the first 429.

The 200s and the repeat 429 are test-only, in
`python/tests/fixtures/anypoint/request_rate_limit/`.

## Served by the simulator

The 429 is the `request-rate-limit` shape in `donkey_kit/simulator/fixtures.py`,
what `donkey mock --scenario request_limit:…` answers once its window is spent.

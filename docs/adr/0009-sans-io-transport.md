# ADR 0009: A sans-IO transport policy shared by both clients, and no default retry of model POSTs on 502/504

- **Status:** Proposed
- **Date:** 2026-10-03
- **Issue:** #728 (part of #707). Recorded under the ADR process from #731.

## Context

`core/transport.py` holds the governed transport: header injection, the retry
loop, the 401 refresh, span recording, the model-substitution check, the SSE
wrappers, and two clients, `DonkeyAsyncClient` and its blocking twin
`DonkeyClient`. The two clients each carry their own copy of the request
pipeline, and the copies have drifted apart before (#728 lists where). Any
change to retries or classification has to be made twice. Other findings in
#728:

- The `simulate()` and conformance seam (`_swap_transport`) writes httpx's
  private `_transport` attribute.
- `_on_request` is a no-op and `_on_refusal` has no caller, so the hook table
  in `ARCHITECTURE.md` ("How the pieces connect") describes seams that do
  nothing.
- `core/errors.py` `classify()` is one long ordered if-chain.
- The httpx2 bridge, which is a transport concern, sits in
  `integrations/_httpx2_bridge.py` and serves only the anthropic adapter.

**Retry of model POSTs.** `_RETRYABLE_STATUS` in `core/transport.py` is
`{502, 503, 504}`, and both clients retry those statuses with backoff, up to
`max_retries`, for every request, unless the gateway marked the response as a
routing fallback (`is_fallback`, #309). A model call is a POST and is not
idempotent: a 502 or 504 from a gateway can come back after the upstream
provider finished the call, so a retry can bill the call twice (#728 names
these two statuses). A transport-level failure (DNS, refused connection, TLS,
timeout) is not retried: it is raised as `GatewayUnavailable`.
`docs/verified-apis.md` records no
idempotency-key header that the gateway or the upstream providers honour on
this path.

## Decision

1. **Transport behaviour lives in pure decision functions.** The retry,
   refusal and substitution decisions take a request and a response (or
   exception) and return what to do next. They do no I/O. The async and sync
   clients only send, sleep and close, and call the same functions.
2. **`core/transport.py` becomes a package** (`headers`, `policy`, `observe`,
   `streaming`, `async_client`, `sync_client`, and the httpx2 bridge moved down
   from `integrations`), with public import paths unchanged.
3. **One parametrised test suite** for headers, retries and refusals runs
   against both clients.
4. **The fixture seam is public.** A `GovernedTransport` owns the inner
   transport and exposes `replace_inner()`, so nothing writes httpx's
   `_transport`.
5. **Hooks that do nothing are wired or deleted.** `_on_request` is deleted;
   `_on_refusal` is wired for #208 or deleted; the `ARCHITECTURE.md` hook table
   is regenerated from what exists.
6. **Retrying a non-idempotent model POST on 502 or 504 is opt-in, and off by
   default.** By default the transport doesn't retry a POST that gets a 502 or
   504. A caller who accepts the risk of a duplicate call opts in through
   config. Everything else keeps today's behaviour: 503 is retried, a 502 or
   504 on an idempotent method is retried, a transport-level failure is not
   retried, and a routing-fallback response is not retried (#309).
7. **No idempotency key until one is verified.** The SDK sends no
   idempotency-key header on model calls until a header the gateway honours is
   confirmed against a real proxy and recorded in `docs/verified-apis.md`
   (`§0.3`). If one is verified, a later ADR can make the retry safe and
   revisit the default.

### Alternatives considered

- **Keep retrying 502/504 on POSTs by default.** Rejected: the cost of a
  duplicate billed call falls on the user, silently, and a transient 502 can
  be retried by the caller who knows whether the call is safe to repeat.
- **Send an idempotency key now.** Rejected: no header is verified for the
  gateway path, and the verification discipline forbids inventing one. A
  header the gateway ignores would also make the retry look safe when it
  isn't.
- **Keep the two clients and test them against each other.** Rejected: a
  test can find drift but not stop it, and every change still has to be
  written twice.

## Consequences

- The change to rule 6 alters what users see on a 502 or 504 from a model
  call: one failed attempt instead of up to `max_retries` attempts. It ships
  with a `MIGRATION.md` entry and the config switch named in the release notes.
- `website/content/errors.mdx` and the retry description in
  `ARCHITECTURE.md` and the `core/transport.py` module docstring say which
  statuses and methods retry.
- Open gap, tracked as #953: when the transport decides not to retry a 502 or
  504 on a model POST (rule 6, an unsafe finish), it doesn't stamp
  `x-should-retry: false` on the response it hands back. Today only a final
  4xx is stamped (`_mark_terminal` in `core/transport.py`, #734). A provider
  SDK above the transport that retries 5xx by itself can therefore re-send the
  POST on top of the transport's decision. Until #953 lands, the adapters'
  SDK-retries-off setting is what prevents it.
- After #728, no module in the transport package exceeds about 400 lines,
  nothing outside core touches `._transport`, and openai>=3 and anthropic>=1
  both go through the core httpx2 bridge.

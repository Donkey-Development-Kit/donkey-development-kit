# Semantic caching — LIVE capture (docs/verified-apis.md §2, #588)

Captured **2026-09-24** from a real deployed Agent Network LLM proxy provisioned
with the **Semantic Caching policy**
(`semantic-caching-policy-openai-azure-ai-search` v1.0.1) in the DDK sandbox,
env **Sandbox** (svc id `14d3b31e-4e3b-4d90-b77a-63c9d6b7ea6a`, from the
`x-envoy-decorator-operation` header). Proxy asset `ddk-semantic-cache`, API
Manager instance **`21195392`**, deployed to the `private-space-omni-gateway`
Flex Gateway. Single OpenAI route (`openai`/`gpt-5-mini`), OpenAI
`text-embedding-3-small` embeddings, an external **Azure AI Search** index +
**Object Store v2** cache, `similarityThreshold` **0.75**, `ttl` **604800s**.

This is the **first live confirmation of the semantic-caching header contract**.
It resolves issue **#588** and unblocks **#587** (steer requests with the
`x-cache-*` headers and surface hit/miss/score on `donkey.last_call`). It is the
caching sibling of the routing captures in `../semantic_routing/` and
`../llm_proxy/`.

## What is verified (all witnessed live)

### Request steering headers — honored, lowercase accepted

All five steering headers were sent **lowercase** (`x-cache-*`) and honored,
confirming the spec table's `X-Cache-*` casing is accepted case-insensitively:

| Request header | Sent | Observed effect |
|---|---|---|
| `x-cache-skip` | `true` | `x-semantic-cache-status: bypass`; upstream called, no lookup/store |
| `x-cache-no-store` | `true` | `x-semantic-cache-status: no-store` + `x-semantic-cache-no-store: true` echoed; upstream served, entry NOT written (proven by the follow-up miss) |
| `x-cache-ttl` | `60` | `x-semantic-cache-ttl: 60` echoed (default is `604800`) |
| `x-cache-threshold` | `0.5` | `x-semantic-cache-threshold: 0.5000` echoed (default `0.7500`) |
| `x-cache-principal-id` | `ddk-verify-588` | `x-semantic-cache-principal-id: ddk-verify-588` echoed; the call **missed** a semantically-near cached entry — the principal partitions the similarity filter |

### Response signal headers — all four status values observed

| Response header | Values seen | Notes |
|---|---|---|
| `x-semantic-cache-status` | `miss` / `hit` / `bypass` / `no-store` | **all four captured** — `responses.miss/hit/bypass/no-store.headers.txt` |
| `x-semantic-cache-score` | `1.0000`, `0.9518`, `0.8918` | **present only on `hit`**; absent on miss/bypass/no-store. Four-dp string |
| `x-semantic-cache-ttl` | `604800`, `60` | present on miss/hit/no-store; **absent on `bypass`**. Echoes an `x-cache-ttl` override |
| `x-semantic-cache-threshold` | `0.7500`, `0.5000` | present on miss/hit/no-store; **absent on `bypass`**. Echoes an `x-cache-threshold` override. Four-dp string |
| `x-semantic-cache-no-store` | `true` | echoed only when `x-cache-no-store` was sent |
| `x-semantic-cache-principal-id` | `ddk-verify-588` | echoed only when `x-cache-principal-id` was sent |

Header-presence by status (observed):

```
status     score   ttl   threshold   no-store(echo)   principal-id(echo)   upstream headers*
miss        —      yes     yes            (if sent)        (if sent)             present
hit        yes     yes     yes              —                —                   ABSENT
bypass      —       —       —               —                —                   present
no-store    —      yes     yes            true               —                   present
```

\* "upstream headers" = `openai-*`, `x-ratelimit-*`, `x-request-id`,
`cf-cache-status` — the OpenAI/Cloudflare response headers. They are **present
on miss/bypass/no-store and ABSENT on hit**, which is itself direct evidence
that a hit makes **no provider round-trip**.

### Cost on hit — proven by verbatim replay, NOT a `total_cost` body field

A `hit` returns the **byte-identical stored completion**:
`responses.hit.body.json` equals `responses.miss.body.json` exactly — same
`chatcmpl-…` id, same `usage` block, same content — served in ~3.0s vs the
miss's ~9.3s. **There is no `total_cost` field in the body** (the issue #588 /
#587 description assumed one); the cached response's original `usage` is
replayed as-is. So the SDK's "zero spend on hit" (#587) must be derived from
`x-semantic-cache-status == hit` (no provider call happened), **not** from a
body cost field. The only cost headers on the wire are the static per-1M rate
hints `x-llm-proxy-input-tokens-cost-per-1m` / `-output-tokens-cost-per-1m`,
which are present on every response (hit and miss alike) and are **not** a
per-request charge. This correction is recorded for #587.

### Shared routing/proxy headers — identical to the model-based case

Every response also carries the model-based routing headers already verified in
`../llm_proxy/` (`x-llm-proxy-routing-type: ModelBased`, `-llm-provider`,
`-llm-model`, `-model-based-routing-success`, `-request-success`) plus
`x-correlation-id` — so the caching policy layers cleanly on top of the existing
proxy contract without altering it.

## How this was captured

Data-plane requests were issued directly against instance `21195392` with the
shared `ddk-test-client` consumer's already-approved contract. Consumer
`client_id`/`client_secret` are **redacted** from `request.success.http` and
were never persisted here. From each captured header dump the Cloudflare
`set-cookie` line (a `__cf_bm` token) and the volatile `cf-ray` id were stripped
as noise; the OpenAI/rate-limit headers were **kept** because their
presence/absence is part of the evidence above.

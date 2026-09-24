# Semantic routing — LIVE capture (docs/verified-apis.md §3, #589)

Captured **2026-09-24** from a real deployed Agent Network LLM proxy provisioned
with **semantic-based routing** (advanced Semantic Service Configuration, an
external Azure AI Search vector store) in the DDK sandbox, env **Sandbox**
(svc id `14d3b31e-4e3b-4d90-b77a-63c9d6b7ea6a`, from the
`x-envoy-decorator-operation` header). Proxy asset `ddk-semantic-advanced-routing`,
API Manager instance **`21194930`**, deployed to the `private-space-omni-gateway`
Flex Gateway. Two topics — **Finance** → `openai`/`gpt-5-mini`, **Code** →
`gemini`/`gemini-2.5-flash` — with `openai` `text-embedding-3-small` embeddings
and a `0.5` fallback threshold.

This is the **first live confirmation of the semantic-routing header contract**.
It resolves issue **#589** and unblocks **#590** (surfacing the matched topic +
similarity score on `donkey.last_call`). It is the semantic sibling of the
model-based routing captured in `../llm_proxy/responses.success.policy-applied.headers.txt`.

## What is verified (all witnessed live)

- **The four shared routing headers are emitted identically to model-based**,
  with `x-llm-proxy-routing-type: Semantic` (vs `ModelBased`) — so the #309
  `LastCall` / `transport` parsing of `routing-type` / `routing-fallback` /
  `llm-provider` / `llm-model` already works unchanged for the semantic case.
- **A semantic-only header carries the match detail:**
  `x-llm-proxy-semantic-routing-success`, with the exact format

  ```
  Request successfully matched '{topic}' topic (Provider: {provider}, Model: {model}). Score: {score}.
  ```

  Note the `(Provider: …, Model: …)` shape — **not** a `provider/model` slug —
  and the bare `0.xx` score. This is the string #590 must parse for
  `matched_topic` + `routing_score`. See `responses.{finance,code,offtopic}.headers.txt`.
- **`x-llm-proxy-request-success`** (`Request completed successfully. Provider:
  …, Model: ….`) and, on the OpenAI-served route, **cost hints**
  `x-llm-proxy-input-tokens-cost-per-1m` / `-output-tokens-cost-per-1m` are also
  present. Those cost headers are recorded but **out of scope for #590**.
- **Client ID Enforcement is on** — same `client_id`/`client_secret` pair as the
  other CIE proxies (no `client_id` → 401 `{"error":"Client ID is not present"}`,
  `www-authenticate: Client-ID-Enforcement`; verified during the probe, not
  re-captured here since it is identical to `../llm_proxy/reject.client-id-missing.*`).

## What could NOT be captured — the fallback branch (§0.3, not fabricated)

`x-llm-proxy-routing-fallback: true` was **not reproducible** on this SSC. With a
10-vector `text-embedding-3-small` store the cosine similarity floors around
`0.51`–`0.53` (above the `0.5` threshold, and the comparison is
strictly-greater-than) even for gibberish and deliberately off-topic prompts —
so every request matched a *primary* topic with `routing-fallback: false`. The
`responses.offtopic.*` capture is such a low-but-above-threshold match
(`Finance`, score `0.51`), **not** an actual fallback. The fallback-branch header
shape therefore remains uncaptured; it would need a proxy whose threshold is
raised (or whose topic coverage is thinner) to observe, which is a
provisioning-side change out of scope for #589.

## How this was captured

The data-plane requests were issued directly against instance `21194930` with
the shared `ddk-test-client` consumer's already-approved contract. Consumer
`client_id`/`client_secret` are **redacted** from `request.success.http` and were
never persisted here; the Cloudflare `set-cookie` line was stripped from the
captured header dumps as noise.

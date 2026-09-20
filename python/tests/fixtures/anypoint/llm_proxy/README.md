# LLM proxy — LIVE captures from a real governed ingress proxy (M0, docs/verified-apis.md §2/§3/§4)

Captured 2026-08-28 from a real deployed Agent Network LLM proxy in the sandbox
org `82a0453b-22e6-430d-bbf4-35b989d043dc`, env **Sandbox**
(`3e6ce455-e3e8-4402-b830-9fcf07d9207b`). The proxy asset is `openai-sdk` 1.0.0,
API Manager instance `21133858`, deployed to the `agent-network-ingress-gw` Flex
Gateway (v1.13.2), upstream `https://api.openai.com/v1/`.

These are the **direct data-plane contract** the SDK's Pillar-1 model access
targets — the first live confirmation of docs/verified-apis.md §§2–4 (previously UNVERIFIED). See
`docs/verified-apis.md` §2/§3/§4/§6.

## Files

- `responses.success.body.json` — HTTP 200 body from
  `POST /openai-sdk/responses` with `{model:"gpt-5.1", input:"…"}`. OpenAI
  **Responses API** object, returned verbatim, incl. the `usage` token block
  (input/output/total tokens) used for cost attribution.
- `responses.success.headers.txt` — response headers (cookies stripped). Note
  `server: Anypoint Flex Gateway`, the `x-llm-proxy-*` governance headers
  (routing-type/provider/model), `x-envoy-decorator-operation`
  (`api-instance-<id>.<envId>.svc`), `x-correlation-id`, and passed-through
  OpenAI `x-ratelimit-*` / `x-request-id` headers.
- `responses.success.policy-applied.headers.txt` — HTTP **200** headers from the
  same instance **after** the `llm-token-rate-limit` policy was applied
  (`maximumTokens:10000`, `timePeriodInMilliseconds:60000`,
  `keySelector:#[attributes.headers['client_id']]`). Unlike
  `responses.success.headers.txt` (captured before any budget policy), the budget
  window here surfaces **only** as prose in `x-llm-proxy-ratelimit`
  (`Token rate limit: … tokens remaining of … limit. Reset in …ms.`) — the numeric
  `x-token-*` trio appears only on the `429`. Non-secret subset published in #352;
  the passed-through `x-ratelimit-*` set is OpenAI's own upstream quota, a distinct
  thing from the gateway policy window (its resets are Go durations, e.g. `0s`).
  Drives the `Budget.observe` prose-fallback regression test.
- `reject.client-id-missing.{body,headers}.json/txt` — HTTP 401 from calling
  without credentials. Anypoint policy envelope `{"error":"Client ID is not
  present"}` + `www-authenticate: Client-ID-Enforcement`.
- `reject.model-not-found.body.json` — HTTP 400 with valid creds + an unroutable
  model. **Upstream OpenAI** error envelope, passed through:
  `{"error":{message,type,param,code}}`.
- `reject.pii-detected.{body.json,headers.txt}` — HTTP **403** from the
  `llm-pii-detection-policy` (applied with `action: Reject`, `entities:["Email"]`)
  when the prompt contained an email. Body is a **nested** object
  `{"error":{"message":"Request contains PII data: […]","type":"pii_detected"}}`
  (no `code`/`param`), and there is **no** `www-authenticate` header — so the 403
  is NOT an auth failure. The gateway `x-llm-proxy-*` routing headers are present
  (the request was matched before the block). Discriminator: error `type` ==
  `pii_detected`.
- `reject.token-rate-limit.{headers.txt,body.empty}` — HTTP **429** from the
  `llm-token-rate-limit` policy (applied with `maximumTokens:1`,
  `timePeriodInMilliseconds:60000`, `keySelector:#[attributes.headers['client_id']]`)
  after the 1-token budget was exhausted. The **body is empty** (`content-length: 0`);
  the budget state is header-only: `x-token-limit`, `x-token-remaining`,
  `x-token-reset` (**milliseconds** until reset). There is **no** standard
  `retry-after`. `.body.empty` is a zero-byte placeholder documenting the empty body.

Both policies above were applied to instance `21133858` only to capture these
rejection contracts and were removed afterward.
- `responses.stream.sample.sse` / `responses.stream.headers.txt` — `stream:true`
  → HTTP 200, `content-type: text/event-stream`, chunked SSE (`event:
  response.created`, `response.in_progress`, …). Confirms docs/verified-apis.md §2 streaming.
- `models.notfound.headers.txt` — `GET /openai-sdk/models` → HTTP 404
  (`Request passed through without model-based routing`). The proxy exposes **no
  catalog endpoint**; it only routes requests carrying a `model` in the body.
- `api_describe.openai-sdk.json` — `api-mgr:api:describe 21133858` (raw REST):
  endpoint type `llm`, upstream + proxyUri, deployment target
  `agent-network-ingress-gw`.
- `policy_list.openai-sdk.json` — `api-mgr:policy:list 21133858`: the full
  governed stack (`cors`, `dataweave-headers-transformation`,
  `client-id-enforcement`, `llm-proxy-core`, `model-based-routing`,
  `openai-transcoding-policy`).

## Auth / secrets

Auth is a `client_id` + `client_secret` **request-header** pair (the
`client-id-enforcement` policy). No credentials or API keys are stored in these
fixtures — response bodies contain none, and the request headers/cookies were
stripped on capture. The deploy-time OpenAI upstream key was passed only via
`--property` and never written to disk.

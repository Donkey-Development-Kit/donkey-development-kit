# Azure OpenAI model-based routing — LIVE capture (docs/verified-apis.md §2, #896)

Captured **2026-10-07** from the real deployed proxy `ddk-azure-openai-model-routing`
in the DDK sandbox, env **Sandbox** (svc id `00000000-0000-4000-8000-5c4fd1a49fc3`,
from the `x-envoy-decorator-operation` header). API Manager instance
**`21188199`**, deployed to the `private-space-omni-gateway` Flex Gateway (the
Azure resource is VNet-firewalled). One OpenAI-format route, model-based,
`azureopenai`/`gpt-5-mini`. Consumer auth is the `client_id`/`client_secret`
pair. UUIDs keep their last 12 hex digits behind the `00000000-0000-4000-8000-`
prefix, as in the sibling captures.

## What is verified (all witnessed live)

- **`POST /responses` → 404** with body `{"error":{"code":"404","message": "Resource not found"}}`
  (Azure's own 404, with its `apim-request-id`). The routing policy **did** match:
  `x-llm-proxy-routing-type`, `x-llm-proxy-llm-provider: azureopenai` and
  `x-llm-proxy-model-based-routing-success` are on the response, and
  `x-llm-proxy-request-success` is **not**. So the proxy, the credentials and
  the model are all fine; the Azure upstream does not serve the Responses API
  on this route. (First seen 2026-09-30, #826; this is the fixture.)
- **`POST /chat/completions` → 200** on the same proxy, credentials and model,
  with a verbatim `object: "chat.completion"` body (including Azure's
  `content_filter_results` / `prompt_filter_results`), `usage`, the Azure
  headers (`x-request-id`, `apim-request-id`, `x-ms-region`, `x-ratelimit-*`)
  and `x-llm-proxy-request-success`. This adds the Azure OpenAI upstream to the
  §2 Chat Completions row.
- **A base path no proxy serves → 404 with an empty body** and **no**
  `x-llm-proxy-*` headers, only `server: Anypoint Flex Gateway` and
  `x-correlation-id`. This is also what every base path returns while the
  gateway's proxies are not deployed. Served base paths answer an
  unauthenticated request with 401 instead.

`donkey doctor` (`python/src/donkey_kit/cli/doctor.py`) tells these apart: the
routed 404 is "`/responses` not served on this route" followed by a
`/chat/completions` probe, and the empty 404 is "no proxy on this base path".

## Files

- `request.success.http` — the three requests (creds redacted).
- `reject.responses-not-served.headers.txt` / `.body.json` — the routed `/responses` 404.
- `responses.chat-completions.success.headers.txt` / `.body.json` — the `/chat/completions` 200.
- `reject.unserved-base-path.headers.txt` — the empty 404 (no body file: the body is empty).

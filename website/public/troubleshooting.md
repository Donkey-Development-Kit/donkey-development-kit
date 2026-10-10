# Troubleshooting

Start with `donkey doctor`. It makes one real governed call and tells apart the
failures that look the same from your code. The sections below explain each
line it prints, then cover the problems `doctor` can't see.

```bash
pip install "donkey-kit[cli]"
donkey doctor                  # or: donkey doctor --model <model-id> --json
```

## Reading `donkey doctor`

Each line starts with a level. Only `[!!]` makes `doctor` exit non-zero, so you
can run it as a CI preflight.

| Glyph | Level | Means |
|---|---|---|
| `[ok]` | ok | The check passed. |
| `[!!]` | fail | The check failed. The next line is the `remediation` text from the typed exception for that failure. |
| `[i] ` | info | Context, such as where an endpoint came from or the budget reading. |
| `[--]` | skip | Not checked, because an earlier check failed. |

The lines, in the order they appear:

| Line | Fails when | What to do |
|---|---|---|
| `config` | A required LLM-proxy field is missing, or the proxy URL may not receive the configured credentials. | The remediation lists every missing field and the env var for each. For the credentials case, see [Which credentials a URL receives](https://docs.donkey-kit.dev/reference/configuration.md#which-credentials-a-url-receives). |
| `llm endpoint`, `control plane` | Never (info). | Check the host and its source: `env`, `project file`, `local overlay`, `user file` or `default`. A value from an unexpected source usually means a stale config file. |
| `plain http` | Never (info). Shown only when `DONKEY_ALLOW_HTTP` is on. | Turn it off outside local testing. See [Endpoints must use `https://`](https://docs.donkey-kit.dev/reference/configuration.md#endpoints-must-use-https). |
| `gateway` | No HTTP response at all (DNS, refused connection, TLS, timeout), or an empty `404` with no proxy on the base path. | See [Wrong URL or no proxy](#wrong-url-or-no-proxy). |
| `credentials` | The gateway answered `401`, or `403` with `www-authenticate`. | See [`401` and `403`](#401-and-403). |
| `model` | The provider rejected the model (the `model_not_found` pass-through). | Request the model from your platform team, or pass another one with `--model`. |
| `responses`, `chat completions` | The route's upstream does not serve `/responses` (an Azure OpenAI route). The run still exits `0` if `/chat/completions` answers. | Call the route with `client.chat.completions.create(...)`. |
| `policy` | Never (info). A policy refused the probe itself, such as a spent budget. | Read the typed refusal. See [Typed refusals](https://docs.donkey-kit.dev/errors.md). |
| `budget` | Never (info). | The reading is only as fresh as the last response, because the proxy has no budget-query endpoint. `observed … ago` says how old it is. |

The [CLI page](https://docs.donkey-kit.dev/cli.md#donkey-doctor) has sample output for a healthy proxy and
for an Azure OpenAI route.

## Failures that look alike

### Wrong URL or no proxy

| Symptom | Cause | Fix |
|---|---|---|
| `GatewayUnavailable` and `doctor` prints `gateway  unreachable` | DNS, a refused connection, TLS or a timeout. No HTTP response came back. | Check the host in `DONKEY_LLM_PROXY_URL`, your network egress and any proxy between you and the gateway. |
| An empty `404`, and `doctor` prints `no proxy on this base path` | The gateway answered, but nothing is deployed at that path. Credentials and model were not checked. | Check the path for a typo, remove a trailing `/v1` (the governed proxy has no `/v1` segment), and confirm the proxy is Active in API Manager. |
| A `404` that carries `x-llm-proxy-*` headers | The proxy routed the model, but the upstream does not serve the API you called. | Use the API the upstream serves. On an Azure OpenAI route, use Chat Completions. |

`DONKEY_LLM_PROXY_URL` is the proxy base with a trailing slash:
`https://<ingress-gw>/<instance>/`. See
[Governed model access](https://docs.donkey-kit.dev/reference/configuration.md#governed-model-access).

### `401` and `403`

| Symptom | Cause | Fix |
|---|---|---|
| `401` with `www-authenticate: Client-ID-Enforcement` | The gateway rejected the `client_id` / `client_secret` pair. | Check `DONKEY_LLM_PROXY_CLIENT_ID` and `DONKEY_LLM_PROXY_CLIENT_SECRET`, and ask your platform team to confirm that this app has access to the proxy. |
| `401` in `jwt` mode | The gateway rejected the JWT, even after one refresh. | See [A `401` in JWT mode](#a-401-in-jwt-mode). |
| `403` with a nested `type: "pii_detected"` | A PII policy blocked the content. This is `PIIDetected`, not an auth failure. | Redact the values listed in `.entities`. |
| `403` with a nested `code: "agent_killed"` | The agent is quarantined. This is `AgentKilled`. | Ask an administrator to restore it. |
| `403` with an `x-llm-proxy-<vendor>-…-action: reject` header | A content-safety policy blocked the content. This is `ContentSafetyBlocked`. | Revise the content listed in `.categories`. |

The SDK reads the body and headers to pick the exception, so catch the typed
exception rather than matching on the status code. The full list is in
[The rejection shapes `classify()` types](https://docs.donkey-kit.dev/errors.md#the-rejection-shapes-classify-types).

### `429`: budget or request rate

Both are `429`, and neither is retried by the SDK.

| Symptom | Exception | Fix |
|---|---|---|
| Empty body with `x-token-limit`, `x-token-remaining` and `x-token-reset` headers | `TokenBudgetExceeded` | Wait for `.retry_after`, or pace calls with [`donkey.budget.pace()`](https://docs.donkey-kit.dev/budget.md). |
| Body `{"error":"Too Many Requests"}` with `x-ratelimit-*` headers and no `x-token-*` headers | `RequestRateLimitExceeded` | Wait for `.retry_after`, or send fewer requests per window. |

CrewAI retries any `429` three times inside its own code. See the
[CrewAI page](https://docs.donkey-kit.dev/frameworks/crewai.md#notes).

### `5xx`

`UpstreamModelError` is a provider failure. The SDK retries a `503`, and
retries a `502` or `504` on a model call only with
[`retry_model_calls_on_gateway_errors`](https://docs.donkey-kit.dev/reference/configuration.md#behaviour).
If it persists, the provider is down: retry later or fall back.

## `donkey.last_call` says `UNAVAILABLE`

`UNAVAILABLE` means the SDK can't see this call's response, so the gateway's
metadata can't be read. It does not mean the call failed.

`donkey.last_call` is filled in from the response as it passes through the
SDK's shared HTTP client. CrewAI builds its own client, so its calls never pass
through. On a `Donkey` whose only resolved adapter is CrewAI, `last_call`
reports `UNAVAILABLE`, and `surface` names the adapter.

| `status` | Means | What to do |
|---|---|---|
| `OBSERVED` | A governed response filled in the record. | Nothing. |
| `UNOBSERVED` | No governed call has returned in this context yet. | Read it after the call, in the same task or `donkey.run()` block. |
| `UNAVAILABLE` | The adapter named in `surface` sends outside the SDK. | Use an adapter whose `capabilities().observes_last_call` is `True` (see [the table](https://docs.donkey-kit.dev/frameworks.md#what-each-factory-gets-capabilities)), or read the gateway's own logs. |

See [`last_call` fields](https://docs.donkey-kit.dev/reference/last-call.md) and
[When `last_call` is unavailable](https://docs.donkey-kit.dev/telemetry.md#when-last_call-is-unavailable).

## A `401` in JWT mode

In `jwt` mode the SDK gets the token from the `llm_auth` provider you pass to
`Donkey(llm_auth=…)`. On a `401` from a model call, the async client calls
`invalidate()` on that provider, sends the request once more with the new
token, and raises `AuthError` if that fails too.

Work through these in order:

1. **No provider.** Without `llm_auth`, every adapter's `connection_kwargs()`
   raises `ConfigError` in `jwt` mode. Pass a provider.
2. **A blocking call.** The blocking client (`sync=True`, or a framework's
   `invoke()` / `complete()`) raises `ConfigError` in `jwt` mode, because the
   token is async-only. Use the async API.
3. **The token never changes.** `StaticToken` does not refresh, so the retry
   sends the same expired token. Use a provider that fetches a new token when
   `invalidate()` is called.
4. **The wallet client ID.** `DONKEY_LLM_PROXY_WALLET_CLIENT_ID` is sent as
   `X-Client-Id` and selects the wallet. It is required in `jwt` mode and is
   not the IdP's client ID.
5. **CrewAI.** CrewAI builds its own client, so the JWT never reaches its
   requests and `donkey.crewai` raises `ConfigError` in `jwt` mode. Use
   client-id auth with CrewAI.

See [JWT / model-wallet auth mode](https://docs.donkey-kit.dev/reference/configuration.md#jwt--model-wallet-auth-mode)
and [Auth providers](https://docs.donkey-kit.dev/reference/configuration.md#auth-providers).

  Still stuck? Run `donkey --json doctor`, remove any secrets from the output,
  and include it in an issue on
  [GitHub](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues).

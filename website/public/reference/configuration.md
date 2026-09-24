# Configuration

`Donkey.from_env()` resolves configuration from, in precedence order: explicit
kwargs → environment variables → `.donkey-kit.toml` → defaults.

## Governed model access

The three required values for the LLM proxy:

| Env var | Meaning |
|---|---|
| `DONKEY_LLM_PROXY_URL` | Proxy base URL: `https://<ingress-gw>/<instance>/` — **no `/v1`**. |
| `DONKEY_LLM_PROXY_CLIENT_ID` | Consumer client ID (the per-agent identity). |
| `DONKEY_LLM_PROXY_CLIENT_SECRET` | Consumer client secret. |

  Auth is a `client_id` / `client_secret` **header pair** (consumer auth), **not**
  a bearer token, and separate from any Anypoint control-plane credential. The
  OpenAI SDK still requires a non-empty `api_key` slot, which the proxy ignores.

  The stock gateway also accepts a **single** colon-joined header —
  `authorization: Bearer <client_id>:<client_secret>` or
  `apikey: <client_id>:<client_secret>` — which its
  `dataweave-headers-transformation` policy splits back into the pair. DDK doesn't use that form: it always
  sends the two-header pair, because `client_id` is the per-agent
  [attribution](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md#cost-attribution-tags) unit. The colon-joined value is not an
  alternative once a `client_id` header is present — the policy ignores it.

Missing required fields are reported **all at once** with their env-var names,
so you can fix configuration in a single pass.

| Optional env var | `DonkeyConfig` field | Meaning |
|---|---|---|
| `DONKEY_LLM_PROXY_KEY` | `llm_proxy_key` | Fills the OpenAI SDK's mandatory `api_key` slot. The proxy ignores it, so leave it unset unless a tool insists on a real-looking value. |

## JWT / model-wallet auth mode

A **model-wallet** proxy identifies the caller from an IdP-issued **JWT** plus a
durable wallet-selector client ID, with Client ID Enforcement disabled and **no
`client_secret`**. Select it with `DONKEY_LLM_PROXY_AUTH=jwt`:

| Env var | `DonkeyConfig` field | Meaning |
|---|---|---|
| `DONKEY_LLM_PROXY_AUTH` | `llm_proxy_auth` | Data-plane auth mode: `client-id` (default) or `jwt`. |
| `DONKEY_LLM_PROXY_WALLET_CLIENT_ID` | `llm_proxy_wallet_client_id` | The wallet's system-generated client ID, sent as the `X-Client-Id` header. Required in `jwt` mode. |

In `jwt` mode the required fields are `llm_proxy_url` **and**
`llm_proxy_wallet_client_id` — **not** `client_id` / `client_secret`. The
rotating JWT is **not** a config value: supply it through an
[`AuthProvider`](#auth-providers) passed as `Donkey(llm_auth=…)`, so the SDK can
re-fetch it as it rotates and refresh it once on a `401`:

```python
from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.auth import StaticToken  # or your own rotating AuthProvider

donkey = Donkey(
    DonkeyConfig(
        llm_proxy_url="https://<ingress-gw>/<instance>/",
        llm_proxy_auth="jwt",
        llm_proxy_wallet_client_id="<wallet-client-id>",
    ),
    llm_auth=StaticToken("<jwt>"),  # rides as Authorization: Bearer <jwt>
)
```

  `jwt` mode is **async-only** — the rotating credential is fetched from an
  async `AuthProvider`, so the blocking client (`sync=True`) is refused with an
  actionable error. Use client-id auth for a synchronous caller. It also works
  only for adapters that use the SDK's shared HTTP client (the raw client and
  LangGraph). Frameworks given a one-time `default_headers` snapshot (ADK,
  CrewAI, LlamaIndex, MS Agent Framework) pin the token at construction and
  can't refresh it — see [Testing](https://donkey-development-kit.github.io/donkey-development-kit/testing.md).

### Auth providers

An `AuthProvider` (in `donkey_kit.core.auth`) is any object with two async
methods: `token()` returns the current credential and `invalidate()` drops a
cached one. The transport calls `invalidate()` and retries exactly once when a
downstream call returns `401`.

| Provider | Use it for |
|---|---|
| `StaticToken(token)` | A token injected out-of-band, for example from CI. Never refreshes. |
| `AnypointConnectedApp(client_id=…, client_secret=…, control_plane_url=…, http_client=…)` | OAuth2 client credentials against the Anypoint token endpoint. Caches the token in memory and refreshes it 60 seconds before expiry. |
| `ChainedAuth(*providers)` | Tries providers in order; the first that yields a token wins. |

For a rotating JWT from your IdP, implement the two methods yourself and pass
the object as `llm_auth`.

## Optional attribution

| Env var | Meaning |
|---|---|
| `DONKEY_APP_NAME` | Human-readable app name, surfaced on telemetry. |
| `DONKEY_BUSINESS_GROUP` | Business group for attribution. |

## Correlation headers

Per-call and per-run correlation IDs ride on request headers. The gateway's
inbound header names aren't published, so the SDK uses placeholder names you
can override to match your gateway. The IDs also appear on spans and
exceptions regardless of the header names. See
[Telemetry & cost](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md#correlation-ids).

| Env var | `DonkeyConfig` field | Meaning |
|---|---|---|
| `DONKEY_CORRELATION_HEADER` | `correlation_header` | Request header that carries the per-run correlation ID. |
| `DONKEY_CALL_ID_HEADER` | `call_id_header` | Request header that carries the per-call ID. |

## Cost-attribution tags

A fixed set of dimensions set once and emitted on every call (both as request
headers and as `donkey.cost.*` span attributes). Override them per run with
`donkey.run(team=…, project=…, env=…, enduser_id=…)`. The key set is fixed — an
unknown dimension is a configuration error, not a silently dropped tag. See
[Telemetry & cost](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md).

| Env var | `Donkey.from_env` kwarg | Meaning |
|---|---|---|
| `DONKEY_COST_TEAM` | `team` | Owning team. |
| `DONKEY_COST_PROJECT` | `project` | Project / workload. |
| `DONKEY_COST_ENV` | `env` | Deployment environment (e.g. `prod`). |
| `DONKEY_COST_ENDUSER_ID` | `enduser_id` | End-user ID (the `enduser.id` tag). |

In `.donkey-kit.toml` these live under a `[donkey.cost]` table (the end-user
dimension keeps its dotted key):

```toml
[donkey.cost]
team = "support"
project = "triage-v2"
env = "prod"
"enduser.id" = "user-42"
```

The request-header **names** the gateway reads for these tags aren't
published, so the SDK uses placeholder names you can override to match your
gateway: `DONKEY_COST_TEAM_HEADER`, `DONKEY_COST_PROJECT_HEADER`,
`DONKEY_COST_ENV_HEADER`, `DONKEY_COST_ENDUSER_HEADER` (or the matching
`cost_*_header` config keys). The `donkey.cost.*` span attributes carry the
full value regardless.

## Telemetry

| Env var | `Donkey.from_env` kwarg | Meaning |
|---|---|---|
| `DONKEY_TELEMETRY` | `telemetry` | Emit OTel spans at all (default `true`). |
| `DONKEY_TELEMETRY_CAPTURE_CONTENT` | `telemetry_capture_content` | Put prompt/completion text on spans (default **`false`**). |

`telemetry_capture_content` defaults to `false` on purpose: spans are emitted
inside your process, **upstream of the gateway's PII masking**, so capturing
content re-exports the very text the platform masks. Enable it only for a
trusted collector. See [Telemetry & cost](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md).

## Behaviour

| Env var | `DonkeyConfig` field | Default | Meaning |
|---|---|---|---|
| `DONKEY_TIMEOUT_S` | `timeout_s` | `60.0` | HTTP timeout for governed calls, in seconds. |
| `DONKEY_MAX_RETRIES` | `max_retries` | `3` | Retries for transient upstream failures (`502` / `503` / `504`) with backoff. Policy refusals are never retried, and a gateway fallback is never retried twice. |
| `DONKEY_ON_MODEL_SUBSTITUTION` | `on_model_substitution` | `off` | `off` surfaces a model substitution on `donkey.last_call`; `raise` turns it into `ModelSubstituted`. See [Telemetry & cost](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md#two-behaviours-worth-knowing). |
| `DONKEY_REGISTRY_CACHE_TTL_S` | `registry_cache_ttl_s` | `300` | How long registry lookups (used by [tool access](https://donkey-development-kit.github.io/donkey-development-kit/tool-access.md)) are cached in memory, in seconds. |
| `DONKEY_NO_CACHE` | — | unset | Set to `1`, `true` or `yes` to bypass that in-memory registry cache. |

## Anypoint control plane

A separate credential from the LLM proxy, used by features that call the
Anypoint control plane (for example, the connected-app token and the
[CLI](https://donkey-development-kit.github.io/donkey-development-kit/cli.md)). You don't need these for governed model access.

| Env var | Meaning |
|---|---|
| `ANYPOINT_CLIENT_ID` | Connected-app client ID. |
| `ANYPOINT_CLIENT_SECRET` | Connected-app client secret. |
| `ANYPOINT_ORG_ID` | Anypoint organization ID. |
| `ANYPOINT_ENV` | Anypoint environment (default `Sandbox`). |
| `ANYPOINT_REGION` | Control-plane region: `us` (default), `eu`, `ca`, or `jp`. |
| `ANYPOINT_BASE_URL` | Explicit control-plane base URL; overrides the region. |

## Config file

Instead of env vars you can put non-secret values in a `[donkey]` table in
`.donkey-kit.toml`, read from the working directory (or `$XDG_CONFIG_HOME`).
Keys are the `DonkeyConfig` field names:

```toml
[donkey]
llm_proxy_url = "https://<ingress-gw>/<instance>/"
llm_proxy_client_id = "…"
```

`donkey init` generates this file from your current environment. Keep secrets
(`llm_proxy_client_secret`, `client_secret`) out of the committed file and
supply them as environment variables.

## Programmatic

```python
from donkey_kit import Donkey, DonkeyConfig

# Explicit config (kwargs win over env):
donkey = Donkey(DonkeyConfig(
    llm_proxy_url="https://<ingress-gw>/<instance>/",
    llm_proxy_client_id="…",
    llm_proxy_client_secret="…",
))

# Or from the environment, with lifecycle:
async with Donkey.from_env() as donkey:
    ...
```

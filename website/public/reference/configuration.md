# Configuration

`Donkey.from_env()` resolves configuration from, in precedence order: explicit
kwargs → environment variables → `.donkey-kit.toml` → defaults.

## Governed model access

The three required values for the LLM proxy:

| Env var | Meaning |
|---|---|
| `DONKEY_LLM_PROXY_URL` | Proxy base URL: `https://<ingress-gw>/<instance>/` — **no `/v1`**. |
| `DONKEY_LLM_PROXY_CLIENT_ID` | Consumer client id (the per-agent identity). |
| `DONKEY_LLM_PROXY_CLIENT_SECRET` | Consumer client secret. |

  Auth is a `client_id` / `client_secret` **header pair** (consumer auth), **not**
  a bearer token, and separate from any Anypoint control-plane credential. The
  OpenAI SDK still requires a non-empty `api_key` slot, which the proxy ignores.

Missing required fields are reported **all at once** with their env-var names, so
you fix configuration in a single pass rather than one error per run.

## JWT / model-wallet auth mode

A **model-wallet** proxy identifies the caller from an IdP-issued **JWT** plus a
durable wallet-selector client id, with Client ID Enforcement disabled and **no
`client_secret`**. Select it with `DONKEY_LLM_PROXY_AUTH=jwt`:

| Env var | `DonkeyConfig` field | Meaning |
|---|---|---|
| `DONKEY_LLM_PROXY_AUTH` | `llm_proxy_auth` | Data-plane auth mode: `client-id` (default) or `jwt`. |
| `DONKEY_LLM_PROXY_WALLET_CLIENT_ID` | `llm_proxy_wallet_client_id` | The wallet's system-generated client id, sent as the `X-Client-Id` header. Required in `jwt` mode. |

In `jwt` mode the required fields are `llm_proxy_url` **and**
`llm_proxy_wallet_client_id` — **not** `client_id` / `client_secret`. The
rotating JWT is **not** a config value: it is supplied through an
[`AuthProvider`](https://donkey-development-kit.github.io/donkey-development-kit/identity.md) passed as `Donkey(llm_auth=…)`, so the SDK can
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
  only for adapters routed through the SDK's shared HTTP client (the raw client
  and LangGraph): frameworks handed a one-time `default_headers` snapshot (ADK,
  CrewAI, LlamaIndex, MS Agent Framework) pin the token at construction and
  cannot refresh it — an [asserted conformance exemption](https://donkey-development-kit.github.io/donkey-development-kit/testing.md).

## Optional attribution

| Env var | Meaning |
|---|---|
| `DONKEY_APP_NAME` | Human-readable app name, surfaced on telemetry. |
| `DONKEY_BUSINESS_GROUP` | Business group for attribution. |

## Cost-attribution tags

A fixed set of dimensions set once and emitted on every call (both as request
headers and as `donkey.cost.*` span attributes). Overridable per run via
`donkey.run(team=…, project=…, env=…, enduser_id=…)`. The key set is fixed — an
unknown dimension is a configuration error, not a silently-dropped tag. See
[Telemetry & cost](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md).

| Env var | `Donkey.from_env` kwarg | Meaning |
|---|---|---|
| `DONKEY_COST_TEAM` | `team` | Owning team. |
| `DONKEY_COST_PROJECT` | `project` | Project / workload. |
| `DONKEY_COST_ENV` | `env` | Deployment environment (e.g. `prod`). |
| `DONKEY_COST_ENDUSER_ID` | `enduser_id` | End-user id (the `enduser.id` tag). |

In `.donkey-kit.toml` these live under a `[donkey.cost]` table (the end-user
dimension keeps its dotted key):

```toml
[donkey.cost]
team = "support"
project = "triage-v2"
env = "prod"
"enduser.id" = "user-42"
```

The gateway-side request-header **names** are unverified, so they are overridable
placeholders — point them at your real names with `DONKEY_COST_TEAM_HEADER`,
`DONKEY_COST_PROJECT_HEADER`, `DONKEY_COST_ENV_HEADER`,
`DONKEY_COST_ENDUSER_HEADER` (or the matching `cost_*_header` config keys). The
`donkey.cost.*` span attributes carry the full value regardless
([Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)).

## Telemetry

| Env var | `Donkey.from_env` kwarg | Meaning |
|---|---|---|
| `DONKEY_TELEMETRY` | `telemetry` | Emit OTel spans at all (default `true`). |
| `DONKEY_TELEMETRY_CAPTURE_CONTENT` | `telemetry_capture_content` | Put prompt/completion text on spans (default **`false`**). |

`telemetry_capture_content` defaults to `false` on purpose: spans are emitted
inside your process, **upstream of the gateway's PII masking**, so capturing
content re-exports the very text the platform masks. Opting in is you assuming
that obligation — enable it only for a trusted collector. See
[Telemetry & cost](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md).

## Anypoint control plane (used by later phases)

These match the Anypoint CLI's own variable names (CLI-verified):
`ANYPOINT_CLIENT_ID`, `ANYPOINT_CLIENT_SECRET`, `ANYPOINT_ORG`, `ANYPOINT_ENV`,
`ANYPOINT_BEARER`, and `ANYPOINT_HOST` (default `anypoint.mulesoft.com`).

## Config file

Instead of env vars you can use `.donkey-kit.toml` in your project. Secrets and
local dev credentials belong in `.donkey-kit.local.toml` (git-ignored), never
in the committed file.

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

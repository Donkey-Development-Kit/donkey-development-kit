# Migration guide

## Next release: credential handling, endpoint trust and printed output

Changes since `0.1.1.dev2` that can affect existing code. The first three
change what the SDK sends or accepts; the rest change only what it prints.

### Each credential stays on its own plane

`Donkey` now keeps one shared HTTP client per credential plane. The data-plane
client (the LLM proxy: `donkey.llm` and every adapter) carries only the
LLM-proxy credential. A separate control-plane client carries the Anypoint
connected-app token (`Donkey(auth=…)`, or the default `AnypointConnectedApp`
built from `ANYPOINT_CLIENT_ID` / `ANYPOINT_CLIENT_SECRET`) and backs the
registry and tool discovery. Model calls no longer fetch the connected-app
token, so they also keep working when the Anypoint token endpoint is
unreachable. Three things change:

**In client-id mode, a `401` from the LLM proxy is terminal.** With Anypoint
credentials configured, the SDK used to refresh the platform token and retry
once. That token is not what the proxy checks, so the retry could not succeed.
The first `401` now surfaces as `AuthError`: check `DONKEY_LLM_PROXY_CLIENT_ID`
/ `DONKEY_LLM_PROXY_CLIENT_SECRET` and the contract in API Manager. In `jwt`
mode the wallet JWT is still refreshed and retried once.

**A token passed as `Donkey(auth=…)` no longer reaches the LLM proxy.** In
client-id mode it used to go out as `Authorization: Bearer …` on model calls
whose client set no `Authorization` of its own (for example the Anthropic and
Gemini clients, or a raw request). If a proxy of yours authenticates model
calls with a bearer token, supply it on the data plane instead: set
`llm_proxy_auth="jwt"` and pass the provider as `Donkey(llm_auth=…)` (see the
JWT / model-wallet section of the configuration reference), or, for a static
key, set `llm_proxy_key` (`DONKEY_LLM_PROXY_KEY`), which the OpenAI SDK sends
as `Authorization: Bearer <key>`:

```diff
- donkey = Donkey(cfg, auth=StaticToken(token))
+ donkey = Donkey(
+     replace(cfg, llm_proxy_auth="jwt", llm_proxy_wallet_client_id=wallet_id),
+     llm_auth=StaticToken(token),
+ )
```

**In `jwt` mode, registry calls carry the connected-app token.** They used to go
out on the data-plane client with the wallet JWT and the `X-Client-Id` wallet
selector. They now carry the connected-app token and no `X-Client-Id`, so
registry and tool discovery need `ANYPOINT_CLIENT_ID` / `ANYPOINT_CLIENT_SECRET`
(or your own `Donkey(auth=…)` provider) in `jwt` mode too.

`simulate()`, `donkey mock` and the conformance kit still act on the data-plane
client, and `aclose()` closes both clients.

### Config files: https endpoints and credential binding

Two config checks now run before the SDK sends a credential, and the
documented secrets file is now read. Full reference:
[Config files, secrets and trust](website/content/reference/configuration.mdx).

#### What changed

1. **Endpoints must be `https://`.** `base_url` / `ANYPOINT_BASE_URL`,
   `llm_proxy_url` / `DONKEY_LLM_PROXY_URL` and the connected-app token endpoint
   must use `https://`. Plain `http://` is still accepted for loopback hosts
   (`localhost`, `127.0.0.0/8`, `::1`), so the local simulator keeps working.
   Anything else raises `ConfigError`.
2. **A URL from the working directory's config files only receives credentials
   from those files.** If `base_url` or `llm_proxy_url` is read from
   `./.donkey-kit.toml` (or `./.donkey-kit.local.toml`) and a credential that
   would be sent there comes from an environment variable, the user config file
   or code, the SDK raises `ConfigError` before sending anything. The error names
   the file, the key and the host. Loopback hosts and the standard Anypoint
   control-plane hosts are exempt. In `jwt` mode the JWT always counts as coming
   from outside the files.
3. **`.donkey-kit.local.toml` is now read.** It is overlaid on
   `./.donkey-kit.toml` with the same `[donkey]` keys; environment variables
   still win over both. When the working directory has neither file,
   `$XDG_CONFIG_HOME/.donkey-kit.toml` is used as before.
4. **Secrets in `.donkey-kit.toml` warn.** `client_secret`,
   `llm_proxy_client_secret` or `llm_proxy_key` in the committed file emit a
   `ConfigWarning` pointing to `.donkey-kit.local.toml`.
5. **`donkey doctor`** prints each endpoint's host and source, and reports a
   binding error on its `config` line instead of making the probe call.

#### Who is affected

A setup is affected if it commits a URL in `.donkey-kit.toml` and keeps the
matching secret in the environment, the most common split before this change
(`donkey init` wrote the URL to the file and told you to keep secrets in env):

```toml
# .donkey-kit.toml
[donkey]
llm_proxy_url = "https://<ingress-gw>/<instance>/"
llm_proxy_client_id = "…"
```

```bash
export DONKEY_LLM_PROXY_CLIENT_SECRET=…   # now: ConfigError naming the file and host
```

Unaffected: URLs and credentials both in environment variables (the usual CI
setup); config built in code with `DonkeyConfig(...)`; a `base_url` on a
standard Anypoint host; loopback URLs; and the user config file.

#### How to migrate

Pick one:

- **Move the secret next to the file** (recommended for local development):

  ```toml
  # .donkey-kit.local.toml — gitignored
  [donkey]
  llm_proxy_client_secret = "…"
  ```

- **Set the URL in the environment as well** (recommended for CI):
  `DONKEY_LLM_PROXY_URL` / `ANYPOINT_BASE_URL`. The environment wins over the file.
- **Opt in** if you trust the directory's config files:
  `export DONKEY_TRUST_PROJECT_CONFIG=1`. It is read only from the
  environment; a config file can't set it.

Also replace any non-loopback `http://` endpoint with its `https://` address,
and move secrets out of a committed `.donkey-kit.toml` into
`.donkey-kit.local.toml` (make sure it is gitignored in your repo).

### Cost-tag headers are opt-in

The cost tags (`team`, `project`, `env`, `enduser.id`) used to go out as
`X-Anypoint-Cost-*` request headers on every call. The LLM Gateway reads none
of them (`docs/verified-apis.md` §3), so they are now sent only when you opt in.
The `donkey.cost.*` span attributes are unchanged and still carry every tag, so
dashboards built on spans need no change. If something of your own reads those
headers, turn them back on:

```diff
+ DONKEY_SEND_COST_HEADERS=true
```

or `send_cost_headers = true` in the `[donkey]` table, or
`DonkeyConfig(send_cost_headers=True)`. The `cost_*_header` name overrides
apply as before.

### `PIIDetected` messages no longer contain the flagged values

`str(exc)` used to be the gateway's rejection text, which repeats each detected
value. It now names the entity types, their count and their offsets, for
example `… 1 entity (Email at chars 12-32) …`. `.entities` is unchanged. If you
parsed the message, read the new `.gateway_message` attribute (the gateway's
text) or `.response` (the raw body) instead. Both carry the blocked content.

### `repr()` / `str()` of `DonkeyConfig` omit the secrets

`client_secret`, `llm_proxy_client_secret` and `llm_proxy_key` no longer
appear. Attribute access and equality are unchanged.

### Printed `connection_kwargs()` show `'***'` for secrets

Every adapter's `connection_kwargs()`, ADK's `gemini_connection_kwargs()` and
`proxy_auth_headers()` now return a `dict` subclass that masks `api_key`, the
`client_secret` header and `Authorization` in `repr()`/`str()`, including in
nested header mappings. Unpacking, lookups, equality and `json.dumps` are
unchanged. If a test compared the printed text, compare the mapping instead.

## `agent-fabric` → `donkey-kit` (the DDK rebrand)

This SDK was renamed from **Agent Fabric SDK** to the **Donkey Development Kit
(DDK)**. The rename is a **trademark** decision, not a functional one:
"Agent Fabric" is a MuleSoft/Salesforce product name, and this project is an
independent, unaffiliated SDK *for consuming* MuleSoft Agent Fabric — the name
cannot be part of our own product identity. The MuleSoft product it targets is
still referred to as "Agent Fabric" throughout the docs; only the SDK's own
identity changed.

**This is a clean break.** Because the SDK is pre-1.0, there are **no
compatibility shims** — no import aliases, no environment-variable fallbacks, no
OpenTelemetry dual-emit. Pin the last `agent-fabric` release if you are not
ready to migrate; otherwise apply every rename below in one pass. The rename is
purely mechanical: no behavior, no wire contract, and no verified API changed.

### At a glance

| Surface | Was | Now |
| --- | --- | --- |
| PyPI distribution | `agent-fabric` | `donkey-kit` |
| Import package | `agent_fabric` | `donkey_kit` |
| Client class / instance | `Fabric` / `fabric` | `Donkey` / `donkey` |
| Other public classes | `FabricConfig`, `FabricError`, `FabricSpec`, `FabricAsyncClient`, `FabricClient` | `DonkeyConfig`, `DonkeyError`, `DonkeySpec`, `DonkeyAsyncClient`, `DonkeyClient` |
| CLI command | `agent-fabric …` | `donkey …` |
| Config file / table / lock | `.agent-fabric.toml` / `[fabric]` / `fabric.lock` | `.donkey-kit.toml` / `[donkey]` / `donkey.lock` |
| Env prefix | `AGENT_FABRIC_*` (and `FABRIC_*`) | `DONKEY_*` |
| OpenTelemetry attribute namespace | `fabric.*` | `donkey.*` |
| pytest plugin entry-point / flag | `agent_fabric_conformance` / `--fabric-conformance` | `donkey_kit_conformance` / `--donkey-conformance` |
| Simulator honesty header | `x-fabric-simulator` | `x-donkey-simulator` |

### 1. Install

```diff
- pip install "agent-fabric[llm,langgraph]"
+ pip install "donkey-kit[llm,langgraph]"
```

Extras keep their names (`llm`, `langgraph`, `cli`, `otel`, `dev`, …); only the
distribution name changed.

### 2. Imports and the client class

```diff
- from agent_fabric import Fabric
- fabric = Fabric.from_env()
- model = fabric.langgraph.chat_model("gpt-4o")
+ from donkey_kit import Donkey
+ donkey = Donkey.from_env()
+ model = donkey.langgraph.chat_model("gpt-4o")
```

Every `Fabric*` public class gains a `Donkey*` name (see the table above); the
raw client is `donkey.llm.client()`. The `donkey.<framework>` factory,
`connection_kwargs()` accessor, and module-level factory keep their shapes —
only the package and instance name changed.

### 3. CLI

```diff
- agent-fabric validate
- agent-fabric plan
+ donkey validate
+ donkey plan
```

All subcommands (`validate`, `plan`, `apply`, `drift`, `lint`, `generate`,
`status`, `init`, `publish`, `verify`) are unchanged apart from the top-level
command name.

### 4. Configuration file

Rename the file, the table header, and the lockfile:

```diff
- # .agent-fabric.toml
- [fabric]
+ # .donkey-kit.toml
+ [donkey]
  llm_proxy_url = "https://<ingress>/<instance>"
```

```diff
- fabric.lock
+ donkey.lock
```

### 5. Environment variables

Every `AGENT_FABRIC_*` variable becomes `DONKEY_*`. There is **no fallback** —
the old names are not read.

| Was | Now |
| --- | --- |
| `AGENT_FABRIC_LLM_PROXY_URL` | `DONKEY_LLM_PROXY_URL` |
| `AGENT_FABRIC_LLM_PROXY_CLIENT_ID` | `DONKEY_LLM_PROXY_CLIENT_ID` |
| `AGENT_FABRIC_LLM_PROXY_CLIENT_SECRET` | `DONKEY_LLM_PROXY_CLIENT_SECRET` |
| `AGENT_FABRIC_LLM_PROXY_KEY` | `DONKEY_LLM_PROXY_KEY` |
| `AGENT_FABRIC_APP_NAME` | `DONKEY_APP_NAME` |
| `AGENT_FABRIC_BUSINESS_GROUP` | `DONKEY_BUSINESS_GROUP` |
| `AGENT_FABRIC_CORRELATION_HEADER` | `DONKEY_CORRELATION_HEADER` |
| `AGENT_FABRIC_CALL_ID_HEADER` | `DONKEY_CALL_ID_HEADER` |
| `AGENT_FABRIC_TIMEOUT_S` | `DONKEY_TIMEOUT_S` |
| `AGENT_FABRIC_MAX_RETRIES` | `DONKEY_MAX_RETRIES` |
| `AGENT_FABRIC_REGISTRY_CACHE_TTL_S` | `DONKEY_REGISTRY_CACHE_TTL_S` |
| `AGENT_FABRIC_TELEMETRY` | `DONKEY_TELEMETRY` |
| `FABRIC_SANDBOX_TESTS` | `DONKEY_SANDBOX_TESTS` |

**Not renamed** — these belong to MuleSoft/Anypoint, not the SDK, and keep
their names:

- `ANYPOINT_CLIENT_ID`, `ANYPOINT_CLIENT_SECRET`, `ANYPOINT_ORG_ID`,
  `ANYPOINT_ENV`, `ANYPOINT_REGION`, `ANYPOINT_BASE_URL` — Anypoint
  control-plane credentials (separate from the LLM proxy's
  `client_id`/`client_secret` consumer-auth pair).
- CI secret names such as `MULESOFT_LLM_PROXY_*` — these are the *upstream*
  MuleSoft-owned secret names; the SDK still reads them into `DONKEY_*` at the
  workflow level.

### 6. Testing surfaces

```diff
- pytest --fabric-conformance --fabric-agent=myagent:build
+ pytest --donkey-conformance --donkey-agent=myagent:build
```

The pytest plugin's entry-point key is now `donkey_kit_conformance`. If you had
pinned the flag or the entry-point name anywhere (tox, CI, a wrapper), update
it. The simulator's honesty header — the one that marks a response as coming
from the local gateway simulator rather than a real gateway — is now
`x-donkey-simulator` (was `x-fabric-simulator`).

### 7. OpenTelemetry (breaking) — the `fabric.*` → `donkey.*` namespace flip

**This is the one change that reaches your observability backend**, so it gets
its own section. Every DDK-specific span name and attribute key moved from the
`fabric.*` namespace to `donkey.*`. There is **no dual-emit**: the old keys stop
appearing the moment you upgrade.

**What did *not* change:** the OpenTelemetry `gen_ai.*` semantic-convention
attributes (`gen_ai.system`, `gen_ai.request.model`,
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, …) and any
`mulesoft.*` attributes are untouched — they are not ours to rename. A governed
model call still opens **one** span carrying both namespaces at once.

Span names:

| Was | Now |
| --- | --- |
| `fabric.llm.chat` | `donkey.llm.chat` |
| `fabric.registry.resolve` | `donkey.registry.resolve` |
| `fabric.tool.call` | `donkey.tool.call` |
| `fabric.provision.apply` | `donkey.provision.apply` |

Attribute keys:

| Was | Now |
| --- | --- |
| `fabric.correlation_id` | `donkey.correlation_id` |
| `fabric.policy.decision` | `donkey.policy.decision` |
| `fabric.policy.type` | `donkey.policy.type` |
| `fabric.budget.remaining` | `donkey.budget.remaining` |
| `fabric.cost.team` | `donkey.cost.team` |

**Action required on your side:** update any dashboards, alerts, saved queries,
span-attribute processors, or sampling rules that key off `fabric.*`. Point them
at `donkey.*`. Queries that only use the `gen_ai.*` attributes need no change.

### What stays the same

- References to **MuleSoft "Agent Fabric"**, **Anypoint**, and the **Omni
  Gateway** — these name the product DDK consumes, not the SDK.
- The wire contract: base URL shape (no `/v1`), the `client_id`/`client_secret`
  header pair, streaming, and the typed rejection shapes.
- The Anypoint CLI plugin literal `mulesoft-anypoint-cli-agent-fabric-plugin`
  and the Maven coordinate `com.mulesoft.agents:agent-fabric-transformation`.
- The `gen_ai.*` / `mulesoft.*` OpenTelemetry attributes and `x-anypoint-*` /
  `x-correlation-id` headers.

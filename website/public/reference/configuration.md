# Configuration

Status: Live. Shipped, and verified against a real governed gateway.

`Donkey.from_env(...)` builds its configuration from the values you pass,
then environment variables, then the config files, then defaults.
`Donkey(DonkeyConfig(...))` uses only what you pass. See [Building a config](#building-a-config) for both paths,
[Precedence](#precedence) for the exact order,
[What the SDK sends where](https://docs.donkey-kit.dev/reference/configuration/control-plane.md#what-the-sdk-sends-where) for which credential goes
to which host, and [Which credentials a URL receives](#which-credentials-a-url-receives)
for the rule that ties a URL from a config file to credentials from the same files.

## Building a config

There are two ways to build a `Donkey`, and they resolve values differently:

| You write | What it reads |
|---|---|
| `Donkey.from_env(...)`, `Donkey()` or `DonkeyConfig.resolve(...)` | The keyword arguments you pass, then environment variables, then the [config files](#config-files-secrets-and-trust), then defaults. Any `DonkeyConfig` field is a keyword argument, and so is `path` (a config file to read in place of `./.donkey-kit.toml`). `Donkey.from_env()` also takes the cost-tag shorthands `team`, `project`, `env` and `enduser_id`. |
| `Donkey(DonkeyConfig(...))` | Only the values you pass. Every other field is its default. No environment variable and no config file is read. |

Building `DonkeyConfig(...)` yourself does not fill the gaps from the
environment. With `DONKEY_TIMEOUT_S=5` set,
`Donkey(DonkeyConfig(llm_proxy_url=…))` still uses the default `timeout_s` of
`60.0`. To change a few fields and keep everything else from the environment
and the files, pass them to `from_env` or `resolve`:

```python
from donkey_kit import Donkey, DonkeyConfig

donkey = Donkey.from_env(telemetry_capture_content=True, registry_cache_ttl_s=60)

# The same config, without building a Donkey:
cfg = DonkeyConfig.resolve(telemetry_capture_content=True, registry_cache_ttl_s=60)
```

The keyword arguments are the `DonkeyConfig` field names, the names in the
"`DonkeyConfig` field" columns below. An unknown name raises `TypeError`, and
the values are checked like any other (see [Invalid values](https://docs.donkey-kit.dev/reference/configuration/attribution.md#invalid-values)).
`DonkeyConfig.from_env()` takes no arguments; it is `DonkeyConfig.resolve()`.
`with_overrides(...)` accepts the same names on a config you already have, and
returns a new config.

## All settings

Every `DonkeyConfig` field except `cost` (set per dimension, see
[Cost-attribution tags](https://docs.donkey-kit.dev/reference/configuration/attribution.md#cost-attribution-tags)), in the order the SDK reads
them. The config-file key goes in the `[donkey]` table of `.donkey-kit.toml`.
This table is generated from the field table in `core/config.py`.

| Env var | `DonkeyConfig` field | Config-file key | Default |
|---|---|---|---|
| `ANYPOINT_CLIENT_ID` | `client_id` | `client_id` | unset |
| `ANYPOINT_CLIENT_SECRET` | `client_secret` | `client_secret` | unset |
| `ANYPOINT_ORG_ID` | `org_id` | `org_id` | unset |
| `ANYPOINT_ENV` | `environment` | `environment` | `Sandbox` |
| `ANYPOINT_REGION` | `region` | `region` | `us` |
| `ANYPOINT_BASE_URL` | `base_url` | `base_url` | from `region` |
| `DONKEY_LLM_PROXY_URL` | `llm_proxy_url` | `llm_proxy_url` | unset |
| `DONKEY_LLM_PROXY_CLIENT_ID` | `llm_proxy_client_id` | `llm_proxy_client_id` | unset |
| `DONKEY_LLM_PROXY_CLIENT_SECRET` | `llm_proxy_client_secret` | `llm_proxy_client_secret` | unset |
| `DONKEY_LLM_PROXY_KEY` | `llm_proxy_key` | `llm_proxy_key` | unset |
| `DONKEY_LLM_PROXY_KEY_HEADER` | `llm_proxy_key_header` | `llm_proxy_key_header` | unset |
| `DONKEY_LLM_PROXY_AUTH` | `llm_proxy_auth` | `llm_proxy_auth` | `client-id` |
| `DONKEY_LLM_PROXY_WALLET_CLIENT_ID` | `llm_proxy_wallet_client_id` | `llm_proxy_wallet_client_id` | unset |
| `DONKEY_USER_TOKEN_HEADER` | `user_token_header` | `user_token_header` | `Authorization` |
| `DONKEY_APP_NAME` | `application_name` | `application_name` | unset |
| `DONKEY_BUSINESS_GROUP` | `business_group` | `business_group` | unset |
| `DONKEY_CORRELATION_HEADER` | `correlation_header` | `correlation_header` | `X-Correlation-Id` |
| `DONKEY_CALL_ID_HEADER` | `call_id_header` | `call_id_header` | `X-Donkey-Request-Id` |
| `DONKEY_COST_TEAM_HEADER` | `cost_team_header` | `cost_team_header` | `X-Anypoint-Cost-Team` |
| `DONKEY_COST_PROJECT_HEADER` | `cost_project_header` | `cost_project_header` | `X-Anypoint-Cost-Project` |
| `DONKEY_COST_ENV_HEADER` | `cost_env_header` | `cost_env_header` | `X-Anypoint-Cost-Env` |
| `DONKEY_COST_ENDUSER_HEADER` | `cost_enduser_header` | `cost_enduser_header` | `X-Anypoint-Cost-Enduser-Id` |
| `DONKEY_TIMEOUT_S` | `timeout_s` | `timeout_s` | `60.0` |
| `DONKEY_MAX_RETRIES` | `max_retries` | `max_retries` | `3` |
| `DONKEY_REGISTRY_CACHE_TTL_S` | `registry_cache_ttl_s` | `registry_cache_ttl_s` | `300` |
| `DONKEY_TOOL_WARN_COUNT` | `tool_warn_count` | `tool_warn_count` | `20` |
| `DONKEY_TOOL_WARN_TOKENS` | `tool_warn_tokens` | `tool_warn_tokens` | `10000` |
| `DONKEY_TELEMETRY` | `telemetry` | `telemetry` | `true` |
| `DONKEY_TELEMETRY_CAPTURE_CONTENT` | `telemetry_capture_content` | `telemetry_capture_content` | `false` |
| `DONKEY_TELEMETRY_INSTALL_GLOBAL` | `telemetry_install_global` | `telemetry_install_global` | `false` |
| `DONKEY_ALLOW_A2A_SDK_TELEMETRY` | `allow_a2a_sdk_telemetry` | `allow_a2a_sdk_telemetry` | `false` |
| `DONKEY_ON_MODEL_SUBSTITUTION` | `on_model_substitution` | `on_model_substitution` | `off` |
| `DONKEY_SEND_COST_HEADERS` | `send_cost_headers` | `send_cost_headers` | `false` |
| `DONKEY_RETRY_MODEL_CALLS_ON_GATEWAY_ERRORS` | `retry_model_calls_on_gateway_errors` | `retry_model_calls_on_gateway_errors` | `false` |

The other pages in this section explain each setting: [Auth modes](https://docs.donkey-kit.dev/reference/configuration/auth.md),
[Attribution, telemetry & behaviour](https://docs.donkey-kit.dev/reference/configuration/attribution.md) and
[Control plane & credentials](https://docs.donkey-kit.dev/reference/configuration/control-plane.md).

## Config files, secrets and trust

Instead of env vars you can put values in a `[donkey]` table. Keys are the
`DonkeyConfig` field names.

| File | Where | Meant for |
|---|---|---|
| `.donkey-kit.toml` | The current working directory (not searched upward), or the file you name with `path=` | Non-secret project config you commit: URLs, client IDs, attribution. |
| `.donkey-kit.local.toml` | The same directory as `.donkey-kit.toml` | Secrets and personal overrides. Keep it gitignored. Same keys. Read with or without `.donkey-kit.toml`. |
| `$XDG_CONFIG_HOME/.donkey-kit.toml`, or `~/.config/.donkey-kit.toml` when `XDG_CONFIG_HOME` is unset, empty or a relative path | Your user config directory | Personal defaults for every project. Always read, beneath the two files above. When `XDG_CONFIG_HOME` is set to an absolute path, `~/.config` isn't read. |

The three files merge key by key: `.donkey-kit.local.toml` over
`.donkey-kit.toml` over the user file, so a lower file fills whatever a higher
one leaves unset. Nested tables such as `[donkey.cost]` merge the same way. A
scalar or an array in a higher file replaces the lower file's value; it isn't
appended to. Each key remembers which file it came from.

Four tables in these files are not `DonkeyConfig` fields, and
`Donkey.from_env()` ignores all four:

- `[donkey.mcp_servers]` declares how each MCP server your tools name is
  reached. `donkey scan` reads it from the scanned directory's two project
  files. See
  [Declaring how an MCP server is reached](https://docs.donkey-kit.dev/registry/publish.md#declaring-how-an-mcp-server-is-reached).
- `[donkey.agents]` declares each agent's protocol and the endpoint it is
  served at, read the same way. See
  [Declaring how an agent is reached](https://docs.donkey-kit.dev/registry/publish.md#declaring-how-an-agent-is-reached).
- `[donkey.publication]` declares the Exchange group ID, asset IDs and version
  to publish under, and each asset's name, description, MCP protocol version,
  provider and documentation file. `donkey publish` reads it from the same two
  files. See
  [Planning a publish](https://docs.donkey-kit.dev/registry/publish.md#planning-a-publish).
- `[donkey.expose]` names the gateway `donkey expose` deploys to, and for
  each service its instance label, upstream and policies. `donkey expose` and
  `donkey preflight` read it from the same two files. See [A2A agents](https://docs.donkey-kit.dev/registry/serve.md).

`Donkey.from_env(path=...)` and `DonkeyConfig.resolve(path=...)` read the named
file in place of `./.donkey-kit.toml`. The `.donkey-kit.local.toml` beside that
file, if there is one, still overlays it, and the user file still sits beneath.
A `path` that names no file raises `ConfigError`. The named file and its
overlay count as project files for the
[endpoint rule](#a-url-from-the-project-files-gets-credentials-from-the-project-files-only).

Both working-directory files must be regular files, or links whose target is
inside the working directory. A link that points elsewhere is refused, because
its keys would be treated as the working directory's:

```text
ConfigError: /home/me/my-agent/.donkey-kit.toml links to /home/me/shared/donkey.toml, outside the working directory. Replace the link with a regular file in the working directory, or set those values in the environment.
```

### Precedence

Highest first, per key:

1. Values set in code: the keyword arguments of `Donkey.from_env(...)` and
   `DonkeyConfig.resolve(...)`, and anything you change on a resolved config,
   with `with_overrides(...)`, `dataclasses.replace(...)` or any other copy.
2. Environment variables.
3. `./.donkey-kit.local.toml` (beside the `path=` file, when you pass one)
4. `./.donkey-kit.toml` (or the `path=` file)
5. The user file (`$XDG_CONFIG_HOME/.donkey-kit.toml`, or
   `~/.config/.donkey-kit.toml`).
6. Defaults.

A keyword argument replaces the field outright: the environment and the files
are not read for it, so an invalid `DONKEY_TIMEOUT_S` is ignored when you pass
`timeout_s`. A `cost=CostTags(...)` argument replaces only the dimensions it
sets. Every invalid value, from any layer, is reported in one `ConfigError`.

A value counts as set in code once it differs from the value that was loaded,
whichever way you changed it; a copy that keeps the value keeps its source.
That includes `DonkeyConfig(**dataclasses.asdict(cfg))` in the same process. A
config rebuilt in another process, for example from a serialized `asdict()`,
keeps where its URLs came from, but its credentials count as set in code. So a
worker in another process (`multiprocessing` spawn, Celery, Ray) that receives
a config with a URL from `.donkey-kit.toml` and credentials from
`.donkey-kit.local.toml` refuses to send them; call `DonkeyConfig.from_env()` in
the worker instead. `DonkeyConfig(...)` built directly reads no environment variable and no file:
every value is the one you pass or the field default. `donkey.run(...)` cost
tags apply on top of all of this for their block. `DONKEY_TRUST_PROJECT_CONFIG`
and `DONKEY_ALLOW_HTTP` are read from the environment only.

```toml
# .donkey-kit.toml (committed)
[donkey]
llm_proxy_url = "https://<ingress-gw>/<instance>/"
llm_proxy_client_id = "my-client-id"
```

```toml
# .donkey-kit.local.toml (gitignored)
[donkey]
llm_proxy_client_secret = "<secret>"
```

`donkey init` generates `.donkey-kit.toml` from your current environment and
never writes a secret into it. If `.donkey-kit.toml` contains `client_secret`,
`llm_proxy_client_secret` or `llm_proxy_key`, the SDK emits a `ConfigWarning`
(`donkey_kit.core.errors.ConfigWarning`) pointing you to
`.donkey-kit.local.toml`. Under `python -W error` or pytest's
`filterwarnings = error`, that warning is raised as an exception.

### Which credentials a URL receives

Two checks run before the SDK sends a credential. Both raise `ConfigError`, and
nothing is sent.

**When they run:** the LLM-proxy check runs when you build a client:
`donkey.llm.client()`, `donkey.openai()`, any `donkey.<framework>` factory, or
`connection_kwargs()`. Creating `Donkey(...)` doesn't run it. The
control-plane check runs at the first control-plane call and again right before
each connected-app token request. `donkey doctor` runs both without sending
anything. For the [`donkey.as_user()`](https://docs.donkey-kit.dev/identity.md) token, the LLM-proxy check
runs when you call `as_user()`. The control-plane check also runs each time a
discovered MCP tool set lists or binds, and before `agent_card()` (and so
[`verify()`](https://docs.donkey-kit.dev/registry/publish/publication.md#verifying-at-runtime-verify))
reads an agent's card. Discovery read those gateway URLs from `base_url`, so
they get `base_url`'s rule for the service's gateway credential, and inside an
`as_user()` block for the user's token as well. `donkey doctor` doesn't run
this gateway check.

**Scope:** the checks cover `llm_proxy_url`, `base_url`, the MCP and A2A gateway
URLs that discovery reads from `base_url`, and the connected-app token
endpoint. A tool set you build by hand has its server URLs in code, so it
isn't checked. A URL you pass to a factory in code gets the `https://` check
when the factory is called; see
[Credentials go only to checked endpoints](https://docs.donkey-kit.dev/reference/configuration/control-plane.md#credentials-go-only-to-checked-endpoints). They don't cover OTLP exporter endpoints (configured by
OpenTelemetry).

The two checks, in the order a credential meets them:

```mermaid
flowchart TD
    subgraph C1["Check 1 · endpoints must use https://"]
        H["Scheme and host"]
        AH["DONKEY_ALLOW_HTTP<br/>in the environment?"]
    end
    subgraph C2["Check 2 · project-file URLs get project-file credentials"]
        P["URL read from .donkey-kit.toml<br/>or .donkey-kit.local.toml, or from<br/>a control plane that base_url names?"]
        T["DONKEY_TRUST_PROJECT_CONFIG<br/>in the environment?"]
        R["base_url on a regional<br/>anypoint.mulesoft.com host?"]
        C["Every credential it would receive<br/>also read from those two files?"]
    end
    H -->|"https://, or http:// to loopback"| P
    H -->|"http:// to another host"| AH
    H -->|"other scheme, or no host"| E1["ConfigError"]
    AH -->|"yes, with a ConfigWarning"| P
    AH -->|no| E1
    P -->|"no: env, user file, code or region default"| OK(["Sent"])
    P -->|yes| T
    T -->|yes| OK
    T -->|no| R
    R -->|yes| OK
    R -->|no| C
    C -->|yes| OK
    C -->|no| E2["ConfigError<br/>names each credential and its source"]
```

A token from `llm_auth` (`jwt` and `bearer` mode), from a `Donkey(auth=…)`
provider or passed to `donkey.as_user()` never comes from a file, so it always
fails the last check. With its URL in a project file, only
`DONKEY_TRUST_PROJECT_CONFIG` (or, for `base_url`, a regional Anypoint host)
lets it through.

#### Endpoints must use `https://`

This applies wherever the URL comes from: environment, file or code. It also
applies to the gateway URL that `resolve_mcp()` and `resolve_agent()` read from
Omni Gateway Manager, which raises `RegistryError` instead of `ConfigError` (see
[Resolving an MCP server](https://docs.donkey-kit.dev/registry/discovery/mcp.md#resolving-an-mcp-server)).

| URL | Result |
|---|---|
| `https://…` | Accepted |
| `http://` on a loopback host: `localhost`, `127.0.0.0/8`, `::1` | Accepted |
| `http://` on any other host, including `0.0.0.0`, LAN addresses and container hostnames such as `http://simulator:8080` | `ConfigError` |
| `http://` on any other host, with `DONKEY_ALLOW_HTTP=1` in the environment | Accepted, with a `ConfigWarning` |
| Any other scheme, or a URL with no host | `ConfigError`, whatever `DONKEY_ALLOW_HTTP` says |
| A URL with a user name or password, such as `https://user:pass@host/` | `ConfigError`, whatever `DONKEY_ALLOW_HTTP` says |

```text
ConfigError: llm_proxy_url must be an https:// URL (got http scheme, llm.example.test). Plain http:// is accepted for loopback hosts (localhost, 127.0.0.0/8, ::1), such as the local gateway simulator. Change it to the https:// address of the service, or set DONKEY_ALLOW_HTTP=1 in the environment to allow plain http:// to other hosts.
```

The SDK sends its own credentials, and an HTTP client would also send a URL's
user name and password to that host. So a URL that carries them is refused on
loopback hosts too. The error names the setting and the host, never the URL;
remove the user name and password from the URL:

```text
ConfigError: llm_proxy_url carries a user name or password (host llm.example.test).
```

`DONKEY_ALLOW_HTTP` (`1`, `true`, `yes` or `on`) is for networks you control,
such as a simulator in another container or an in-cluster proxy without TLS.
Credentials sent over plain `http://` travel unencrypted, so don't use it across
a network you don't trust. It is read from the environment only; a `[donkey]`
key with that name is ignored. It changes only the `https://` rule: a URL from
the project files still receives only credentials from those files (next
section). When it lets a URL through, the SDK emits a `ConfigWarning` naming the
key (`base_url`, `llm_proxy_url` or `token endpoint`), or the gateway and asset
of a resolved URL, and the host. Python shows
each distinct warning once per location by default:

```text
ConfigWarning: llm_proxy_url uses plain http:// to llm.example.test because DONKEY_ALLOW_HTTP=1 is set. Credentials and data sent to it are not encrypted in transit.
```

The warning quotes the value as you set it, for example
`DONKEY_ALLOW_HTTP=true is set`. `donkey doctor` adds a `plain http` line while
it is on. See
[CLI](https://docs.donkey-kit.dev/cli/setup.md#donkey-doctor).

#### A URL from the project files gets credentials from the project files only

An endpoint URL read from `./.donkey-kit.toml` or `./.donkey-kit.local.toml`
only receives credentials read from those two files. This applies to loopback
URLs too: they are exempt from the `https://` rule only.

These are the credentials checked for each endpoint:

| Endpoint | Credentials checked |
|---|---|
| `base_url` | `client_id`, `client_secret`, or the token from a `Donkey(auth=…)` provider, which always counts as coming from outside the files |
| `llm_proxy_url`, client-id mode | `llm_proxy_client_id`, `llm_proxy_client_secret`, `llm_proxy_key` (the ones that are set) |
| `llm_proxy_url`, `jwt` mode | `llm_proxy_wallet_client_id`, and the JWT from `llm_auth`, which always counts as coming from outside the files |
| `llm_proxy_url`, `bearer` mode | `llm_proxy_key` if set, and the token from `llm_auth`, which always counts as coming from outside the files |
| `llm_proxy_url`, inside `donkey.as_user()` | The `as_user()` token, in every mode, which always counts as coming from outside the files |
| `base_url`, for the gateway URL of a discovered MCP tool set or of `agent_card()` | The service's gateway credential: `llm_proxy_client_id` and `llm_proxy_client_secret` in client-id mode; the token from `llm_auth` in `jwt` and `bearer` mode; or a `discover(auth=…)` credential. The last two always count as coming from outside the files |
| `base_url`, for a discovered MCP tool set listed or bound inside `donkey.as_user()` | The `as_user()` token, which always counts as coming from outside the files, and the gateway credential headers that still go with it |

| The URL comes from | A credential it would receive comes from | Result |
|---|---|---|
| Environment, user file, code, or the region default | Anywhere | Sent |
| `.donkey-kit.toml` / `.donkey-kit.local.toml` | `.donkey-kit.toml` / `.donkey-kit.local.toml` | Sent |
| `.donkey-kit.toml` / `.donkey-kit.local.toml` | An environment variable | `ConfigError` |
| `.donkey-kit.toml` / `.donkey-kit.local.toml` | The user file | `ConfigError` |
| `.donkey-kit.toml` / `.donkey-kit.local.toml` | Code (a value you set or changed on the config) | `ConfigError` |
| `.donkey-kit.toml` / `.donkey-kit.local.toml`, `jwt` mode | The JWT from `llm_auth` (always) | `ConfigError`, every time |
| `.donkey-kit.toml` / `.donkey-kit.local.toml`, `bearer` mode | The token from `llm_auth` (always) | `ConfigError`, every time |
| `.donkey-kit.toml` / `.donkey-kit.local.toml` (`base_url`) | The token from a `Donkey(auth=…)` provider (always) | `ConfigError`, every time |
| `.donkey-kit.toml` / `.donkey-kit.local.toml` (`llm_proxy_url`, or `base_url` for a discovered MCP tool set) | The `donkey.as_user()` token (always) | `ConfigError`, every time |
| `.donkey-kit.toml` / `.donkey-kit.local.toml` (`base_url`, for a discovered MCP tool set or `agent_card()`) | A `discover(auth=…)` credential (always) | `ConfigError`, every time |
| `.donkey-kit.toml` / `.donkey-kit.local.toml`, `base_url` on `anypoint.mulesoft.com`, `eu1.anypoint.mulesoft.com`, `ca1.platform.mulesoft.com` or `jp1.platform.mulesoft.com` | Anywhere | Sent |
| Any of the above, with `DONKEY_TRUST_PROJECT_CONFIG` set in the environment | Anywhere | Sent |
| Any of the above, with only `DONKEY_ALLOW_HTTP` set | As in the rows above | Unchanged: `DONKEY_ALLOW_HTTP` affects the `https://` rule only |

**What counts as code:** any value passed to `DonkeyConfig(...)`; any value on
a resolved config that differs from what was loaded, whether you changed it with
`with_overrides(...)`, `dataclasses.replace(...)` or otherwise; the token from a
`Donkey(auth=…)` provider; the token passed to `donkey.as_user()`; a credential
passed to `donkey.tools.discover(auth=…)`; and in `jwt` and `bearer` mode the
token returned by `llm_auth`. A
config built entirely with `DonkeyConfig(...)` has its URLs in code too, so the
rule never applies to it. Likewise, a URL you change in code is no longer
treated as coming from the file.

Both `jwt` and `bearer` modes check the data-plane credential endpoint before
every provider token request, including raw sends through the shared client.
User-token sends also check the receiving client's configuration on every send,
not only the configuration that created the `as_user()` scope.

The error names every credential that came from elsewhere, where each came
from, the host, the key and the file:

```text
ConfigError: Not sending llm_proxy_client_secret (from env) to llm-proxy.example.com: llm_proxy_url is set in /home/me/my-agent/.donkey-kit.toml, and credentials from outside the project config files are only sent to hosts those files name when you opt in. To continue, do one of:
  - set the URL in the environment instead (DONKEY_LLM_PROXY_URL=https://...)
  - keep the credentials in /home/me/my-agent/.donkey-kit.local.toml, next to the project file
  - trust the project config files by setting DONKEY_TRUST_PROJECT_CONFIG=1
```

The source labels are `env`, `user file` and `code`.

#### Resolving it

1. **Set the URL in the environment** (`DONKEY_LLM_PROXY_URL` /
   `ANYPOINT_BASE_URL`). The environment wins over the file, so the URL no
   longer comes from the file.
2. **Keep the credentials in `.donkey-kit.local.toml`** in the same directory.
   Not available for the `llm_auth` token in `jwt` or `bearer` mode, a
   `Donkey(auth=…)` token, a `discover(auth=…)` credential or the
   `donkey.as_user()` token, because none of them comes from a file.
3. **Opt in:** set `DONKEY_TRUST_PROJECT_CONFIG` to `1` (or `true`, `yes`,
   `on`) in the environment if you trust the project config files. The
   setting decides whether those files are trusted, so it is read from the
   environment only; a `[donkey]` key with that name is ignored.

In `jwt` or `bearer` mode with the URL in a project file, for a `Donkey(auth=…)` token
or a `discover(auth=…)` credential with `base_url` in a project file, and for the
`donkey.as_user()` token, only 1 and 3 apply; the error lists only those two. The error names every token it
holds back, for example
`Not sending the token from the llm_auth provider, the donkey.as_user() token to …`.

`donkey doctor` prints each endpoint's host and where it came from (`env`,
`project file`, `local overlay`, `user file` or `default`), and reports this
error on its `config` line without sending a request. See
[CLI](https://docs.donkey-kit.dev/cli/setup.md#donkey-doctor).

## Programmatic

```python
import dataclasses

from donkey_kit import Donkey, DonkeyConfig

# Entirely in code: no environment variable or config file is read.
donkey = Donkey(DonkeyConfig(
    llm_proxy_url="https://<ingress-gw>/<instance>/",
    llm_proxy_client_id="my-client-id",
    llm_proxy_client_secret="<secret>",
))

# Resolved from env and files, with code values on top. Each form records
# the value as set in code:
cfg = DonkeyConfig.resolve(send_cost_headers=True)
cfg = DonkeyConfig.from_env().with_overrides(send_cost_headers=True)
cfg = dataclasses.replace(DonkeyConfig.from_env(), send_cost_headers=True)

# From a named config file instead of ./.donkey-kit.toml:
cfg = DonkeyConfig.resolve(path="deploy/staging.toml")

# Or from the environment, with lifecycle:
async with Donkey.from_env() as donkey:
    ...
```

## What printed output hides

The SDK keeps credential values out of `repr()` and `str()`, so printing,
logging, a test-failure diff or a traceback with locals doesn't show them. It
only changes how objects are printed: every value is still there, and the SDK
sends the real values.

| Object | Hidden when printed | Still shown |
|---|---|---|
| `DonkeyConfig` | `client_secret`, `llm_proxy_client_secret` and `llm_proxy_key` are left out | Every other field, including client IDs, `llm_proxy_wallet_client_id`, URLs and `cost=CostTags(…, enduser_id=…)` |
| Every adapter's `connection_kwargs()`, ADK's `gemini_connection_kwargs()`, `donkey_kit.core.transport.proxy_auth_headers()` | The value under any key named `api_key`, `apikey`, `api-key`, `x-api-key`, `x-goog-api-key`, `client_secret`, `authorization`, `proxy-authorization` or `cookie` (any case), at any depth, shows as `'***'` | `base_url`, `client_id`, `X-Client-Id`, attribution headers, and `X-Anypoint-Cost-*` headers including the end-user ID |
| `PIIDetected` (`str`, `repr`, `.args`) | The flagged values | Entity types, count and character offsets; see [Errors](https://docs.donkey-kit.dev/errors.md#refusal-messages-dont-repeat-blocked-content) |

The masked mappings are ordinary `dict`s. The table shows how common operations behave:

| Operation | Result |
|---|---|
| `**kwargs`, `kwargs["api_key"]`, `==`, passing it to a framework | Real values |
| `.copy()`, `copy.copy`, `copy.deepcopy`, `kwargs \| other` | Still masked when printed |
| `dict(kwargs)`, `{**kwargs}`, `kwargs.items()` | Top level printed in full, including `api_key`. Nested header mappings stay masked. |
| `json.dumps(kwargs)` | Writes the **real** values |

  Some framework objects built from the kwargs print credentials in their own
  `repr()` / `str()`:

  | Object | Prints |
  |---|---|
  | LangGraph `ChatOpenAI` (`donkey.langgraph.chat_model()`) | `client_secret` |
  | LlamaIndex `OpenAILike` (`donkey.llamaindex.llm()`) | `client_secret` and `api_key` |
  | CrewAI `OpenAICompletion` (`donkey.crewai.llm()`) | `api_key` |

  Don't print or log these objects, and don't leave them in locals that a
  traceback renders.

Not covered by the masking:

- Attribute access and conversions: `config.client_secret`,
  `dataclasses.asdict(config)`, `vars(...)`. (Where each value came from is
  kept as a keyed digest, so `asdict()` holds each secret once, in its own
  field.)
- Objects built from the kwargs, such as a framework client or the
  `openai_client` returned by the OpenAI Agents adapter, and whatever those
  objects log.
- `attribution_headers()` and `cost_headers()`, which return plain `dict`s.
- `.response` on any `DonkeyError`: `exc.response.request.headers` holds the
  real `client_secret` and `Authorization` headers.
- `PIIDetected.gateway_message` and `.response`, which contain the flagged
  values.
- `UpstreamRequestError` messages, which include the upstream provider's own
  error text and can quote parts of your request.
- OpenTelemetry spans: cost tags, including `donkey.cost.enduser.id`, and
  message content when `telemetry_capture_content` is on.

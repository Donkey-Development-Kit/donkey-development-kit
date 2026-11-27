# Attribution, telemetry & behaviour

Status: Live. Shipped, and verified against a real governed gateway.

## Optional attribution

| Env var | `DonkeyConfig` field | Sent as |
|---|---|---|
| `DONKEY_APP_NAME` | `application_name` | The `X-Anypoint-Client-Application` request header. |
| `DONKEY_BUSINESS_GROUP` | `business_group` | The `X-Anypoint-Business-Group` request header. |

These two values go only on request headers. No span attribute carries them.
The gateway attributes traffic to the `client_id` credential (or, in `jwt`
mode, the JWT's `client_id` claim), so you don't need either value for
attribution.

To set the business group per request rather than per `Donkey`, wrap the calls
in `donkey.attribution(business_group=…)` or pass `business_group=` to
`donkey.run(...)`. The scoped value wins over this field for its block. See
[Per-request attribution](https://docs.donkey-kit.dev/cost-attribution.md#per-request-attribution).

  Both header names are unconfirmed guesses. The gateway hasn't been seen
  reading them (see §3 of the
  [verification ledger](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md)).
  The first time the SDK sends one,
  it emits an `UnverifiedValueWarning`. Unlike the correlation and cost-tag
  headers, these names have no config key, so you can't override them. To
  stop the warning, leave both values unset.

## Correlation headers

Per-call and per-run correlation IDs ride on request headers. The IDs also
appear on spans and exceptions regardless of the header names. See
[Telemetry & cost](https://docs.donkey-kit.dev/correlation.md).

- `X-Correlation-Id` carries the run ID. The gateway reads this header and
  echoes it on the response `x-correlation-id`, so a client log line joins to
  the gateway's record.
- `X-Donkey-Request-Id` carries the per-call ID. The name is the SDK's own
  convention, and the gateway doesn't read it.

Both are confirmed names, so neither emits a warning. Override one only if a
proxy of your own in front of the gateway expects a different name:

| Env var | `DonkeyConfig` field | Default | Meaning |
|---|---|---|---|
| `DONKEY_CORRELATION_HEADER` | `correlation_header` | `X-Correlation-Id` | Request header that carries the per-run correlation ID. |
| `DONKEY_CALL_ID_HEADER` | `call_id_header` | `X-Donkey-Request-Id` | Request header that carries the per-call ID. |

### Header names you can't use

`correlation_header`, `call_id_header` and the four `cost_*_header` keys
([Cost-attribution tags](#cost-attribution-tags)) are checked whenever a config
is built, whether from the environment, a file or code. Names compare
case-insensitively. A name must start with `X-` (all the defaults do), and a
key can't name:

| Header names | Why |
|---|---|
| `Host`, `Forwarded`, any `X-Forwarded-*`, `X-Real-IP`, `Content-Length`, `Transfer-Encoding`, `Connection`, `Upgrade`, `TE`, `Trailer`, `Expect` | They decide where the request goes or how its body is framed. |
| `X-HTTP-Method-Override`, `X-HTTP-Method`, `X-Method-Override`, `X-Original-URL`, `X-Original-URI`, `X-Rewrite-URL` | Some servers and gateways read them to change the request's method or path. |
| `Authorization`, `Proxy-Authorization`, `Cookie`, `x-api-key`, `api-key`, `apikey`, `api_key`, `x-goog-api-key`, `client_id`, `client_secret`, `X-Client-Id` | They carry or select credentials. |
| `X-Anypoint-Client-Application`, `X-Anypoint-Business-Group`, the `x-cache-*` headers, any `x-stainless-*`, `Content-Type`, `Accept`, `Accept-Encoding`, `User-Agent` | The SDK, its HTTP client or a framework's SDK already sets them. |
| A name another of these keys already uses, including its default | Two values would share one header. |
| Anything that isn't a valid HTTP header name (for example, one with a space) | It can't be sent. |
| Any other name that doesn't start with `X-` (for example `Team`, `Origin`, `Range`) | Standard and framework headers (`Content-Encoding`, `Via`, `anthropic-version`, …) change how the request is handled. |

```text
ConfigError: cost_team_header names the header 'Host', which can't be used: it controls where the request goes or how it is framed. cost_team_header is set in the environment (DONKEY_COST_TEAM_HEADER). Choose a different header name.
```

## Cost-attribution tags

A fixed set of dimensions set once and emitted on every call as `donkey.cost.*`
span attributes (and, only if you opt in, as request headers). Override them per run with
`donkey.run(team=…, project=…, env=…, enduser_id=…)`. The key set is fixed — an
unknown dimension is a configuration error, not a silently dropped tag. See
[Telemetry & cost](https://docs.donkey-kit.dev/telemetry.md).

These are the `cost` field (a `CostTags`). `Donkey.from_env` takes each
dimension as a shorthand kwarg; `DonkeyConfig.resolve(cost=CostTags(...))` sets
them together. Either way, a dimension you pass replaces only that dimension.

| Env var | `Donkey.from_env` kwarg | Meaning |
|---|---|---|
| `DONKEY_COST_TEAM` | `team` | Owning team. |
| `DONKEY_COST_PROJECT` | `project` | Project / workload. |
| `DONKEY_COST_ENV` | `env` | Deployment environment (e.g. `prod`). |
| `DONKEY_COST_ENDUSER_ID` | `enduser_id` | End-user ID (the `enduser.id` tag). |

In a config file these live under a `[donkey.cost]` table (the end-user
dimension keeps its dotted key):

```toml
[donkey.cost]
team = "support"
project = "triage-v2"
env = "prod"
"enduser.id" = "user-42"
```

A `[donkey.cost]` table merges across the config files key by key: a local
`"enduser.id"` adds to the project file's `team` and `project` instead of
replacing them, and the user file fills any dimension neither sets. `DONKEY_COST_*`
variables then override single dimensions.

The LLM Gateway doesn't read cost tags from request headers, so by default DDK
sends **no** cost-tag header. The `donkey.cost.*` span attributes carry every
tag either way. To also send the tags as request headers, for example to a
proxy of your own that reads them, opt in:

| Env var | `DonkeyConfig` field | Default | Meaning |
|---|---|---|---|
| `DONKEY_SEND_COST_HEADERS` | `send_cost_headers` | `false` | Also send the cost tags as request headers. |

With it enabled, the headers use the SDK's own names (`X-Anypoint-Cost-Team`,
…). Because no gateway reads them, they're a convention rather than a
placeholder for some real name, and they emit no warning. Rename them to match
whatever receiver you send them to, within the
[names you can't use](#header-names-you-cant-use):

| Env var | `DonkeyConfig` field | Default |
|---|---|---|
| `DONKEY_COST_TEAM_HEADER` | `cost_team_header` | `X-Anypoint-Cost-Team` |
| `DONKEY_COST_PROJECT_HEADER` | `cost_project_header` | `X-Anypoint-Cost-Project` |
| `DONKEY_COST_ENV_HEADER` | `cost_env_header` | `X-Anypoint-Cost-Env` |
| `DONKEY_COST_ENDUSER_HEADER` | `cost_enduser_header` | `X-Anypoint-Cost-Enduser-Id` |

In code, `Donkey.from_env(send_cost_headers=True)`.

### Custom cost dimensions

Dimensions beyond the fixed four are declared once, by name, and their values
set per request with `donkey.attribution(cost_center=…)`. See
[Custom dimensions](https://docs.donkey-kit.dev/cost-attribution.md#custom-dimensions) for the rules and the
cardinality ceiling.

| Env var | `DonkeyConfig` field | Default | Meaning |
|---|---|---|---|
| `DONKEY_COST_DIMENSIONS` | `cost_dimensions` | `()` | The custom dimension names, comma-separated in the environment and a list in code or a config file. At most 10. |
| `DONKEY_HIGH_CARDINALITY_COST_DIMENSIONS` | `high_cardinality_cost_dimensions` | `()` | Declared dimensions exempt from the 1000-distinct-values ceiling. Each must also be in `cost_dimensions`. |

Each custom dimension's header, sent only with `send_cost_headers`, is
`X-Anypoint-Cost-` plus the title-cased name (`cost_center` →
`X-Anypoint-Cost-Cost-Center`) and can't be renamed.

  With `send_cost_headers` on, the headers, including the end-user ID, go only
  on model requests to the LLM proxy, never to the Anypoint platform. Where the
  tags go is described in
  [Telemetry & cost](https://docs.donkey-kit.dev/cost-attribution.md).

## Telemetry

| Env var | `DonkeyConfig` field | Meaning |
|---|---|---|
| `DONKEY_TELEMETRY` | `telemetry` | Emit OTel spans at all (default `true`). |
| `DONKEY_TELEMETRY_CAPTURE_CONTENT` | `telemetry_capture_content` | Put prompt/completion text on spans; since 0.2.1, also permit outbound A2A quality explanations (default **`false`**). |
| `DONKEY_TELEMETRY_INSTALL_GLOBAL` | `telemetry_install_global` | Install DDK's OTLP provider as the process-global OpenTelemetry provider (default **`false`**). See [DDK leaves the global provider to you](https://docs.donkey-kit.dev/telemetry.md#ddk-leaves-the-global-provider-to-you). |
| `DONKEY_ALLOW_A2A_SDK_TELEMETRY` | `allow_a2a_sdk_telemetry` | Permit outbound A2A calls with enabled SDK tracing (default **`false`**), accepting that it may export sensitive diagnostics. Independent of DDK's `telemetry` setting. |

`telemetry_capture_content` defaults to `false` on purpose: spans are emitted
inside your process, **upstream of the gateway's PII masking**, so capturing
content re-exports the very text the platform masks. Enable it only for a
trusted collector. New in 0.2.1, the same opt-in also
exports gateway quality explanations on outbound A2A spans. These may repeat
request text and are not sanitized; they are truncated to 4096 characters,
which is not redaction. See [Telemetry & cost](https://docs.donkey-kit.dev/telemetry.md).

## Behaviour

| Env var | `DonkeyConfig` field | Default | Meaning |
|---|---|---|---|
| `DONKEY_TIMEOUT_S` | `timeout_s` | `60.0` | HTTP timeout for governed calls, in seconds. |
| `DONKEY_MAX_RETRIES` | `max_retries` | `3` | Retries for transient upstream failures (`502` / `503` / `504`) with backoff. A `502` or `504` on a model call is retried only with `retry_model_calls_on_gateway_errors` (below). Policy refusals are never retried, and a gateway fallback is never retried twice. |
| `DONKEY_RETRY_MODEL_CALLS_ON_GATEWAY_ERRORS` | `retry_model_calls_on_gateway_errors` | `false` | Also retry a `502` or `504` on a model call (a `POST` whose body, or native Gemini path, names a model). Off by default, because the provider may already have completed and billed the call, and the gateway offers no verified idempotency key. A `503`, and other requests outside scoped A2A, are retried either way. A2A never automatically replays. |
| `DONKEY_ON_MODEL_SUBSTITUTION` | `on_model_substitution` | `off` | `off` surfaces a model substitution on `donkey.last_call`; `raise` turns it into `ModelSubstituted`. See [Telemetry & cost](https://docs.donkey-kit.dev/frameworks/routing.md#two-behaviours-worth-knowing). |
| `DONKEY_REGISTRY_CACHE_TTL_S` | `registry_cache_ttl_s` | `300` | How long registry lookups (used by [MCP tools](https://docs.donkey-kit.dev/tool-access.md)) are cached in memory, in seconds. |
| `DONKEY_TOOL_WARN_COUNT` | `tool_warn_count` | `20` | Warn when a [tool binding](https://docs.donkey-kit.dev/tool-access/binding.md#what-the-tool-list-costs) exposes more tools than this. `0` turns the warning off. See [Tool-set size warnings](#tool-set-size-warnings). |
| `DONKEY_TOOL_WARN_TOKENS` | `tool_warn_tokens` | `10000` | Warn when a tool binding's descriptors cost more than this many estimated tokens per model call. `0` turns the warning off. |
| `DONKEY_NO_CACHE` | — | unset | Set to `1`, `true` or `yes` to bypass that in-memory registry cache. |
| `DONKEY_TRUST_PROJECT_CONFIG` | — | unset | Set to `1` (or `true`, `yes`, `on`) to let a URL from the working directory's config files receive credentials from elsewhere. Read only from the environment. See [Which credentials a URL receives](https://docs.donkey-kit.dev/reference/configuration.md#which-credentials-a-url-receives). |
| `DONKEY_ALLOW_HTTP` | — | unset | Set to `1` (or `true`, `yes`, `on`) to allow plain `http://` endpoints on non-loopback hosts. Read only from the environment. See [Endpoints must use `https://`](https://docs.donkey-kit.dev/reference/configuration.md#endpoints-must-use-https). |

### Tool-set size warnings

Every model call carries the descriptors of the tools it can call, so a large
tool set costs tokens on every call and makes the model worse at picking the
right tool. Each `ToolSet` binding logs its tool count and
`descriptor_tokens()` estimate at debug level, and logs a warning (logger
`donkey_kit.tools.session`) when the set passes `tool_warn_count` or
`tool_warn_tokens`. The defaults come from the model vendors' published
guidance:

| Default | Source | Guidance |
|---|---|---|
| `tool_warn_count = 20` | [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling) | "Aim for fewer than 20 functions available at the start of a turn at any one time, though this is just a soft suggestion." |
| `tool_warn_count = 20` | [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling) | "Tool Selection: Keep active set to 10-20 tools maximum." |
| `tool_warn_tokens = 10000` | [Claude tool search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) | Use tool search when "Your tool definitions consume more than 10k tokens." |

A set at a threshold does not warn, only one above it. Narrow the set with
[`filter()`](https://docs.donkey-kit.dev/tool-access/binding.md#filtering), raise a threshold, or set it to
`0` to turn that warning off.

### Invalid values

Every `DonkeyConfig` is checked when it is built, including by
`with_overrides()`, and an invalid value raises `ConfigError` before any
request is sent. One error lists every bad field and where each was set:

| Field | Accepted |
|---|---|
| `timeout_s` | A number greater than `0` |
| `max_retries`, `registry_cache_ttl_s`, `tool_warn_count`, `tool_warn_tokens` | A whole number, `0` or more |
| `telemetry`, `telemetry_capture_content`, `telemetry_install_global`, `send_cost_headers`, `retry_model_calls_on_gateway_errors` | `True` or `False` in code; `1`, `true`, `yes`, `on`, `0`, `false`, `no` or `off` (any case) in the environment or a config file |
| `region` | `us`, `eu`, `ca` or `jp` |
| `llm_proxy_auth` | `client-id`, `jwt` or `bearer` (any case in the environment or a config file) |
| `on_model_substitution` | `off` or `raise` (any case in the environment or a config file) |
| `cost_dimensions`, `high_cardinality_cost_dimensions` | At most 10 names of 1-32 lowercase letters, digits and underscores, each starting with a letter, none repeated and none a fixed dimension; every `high_cardinality_cost_dimensions` name must also be in `cost_dimensions` |

A misspelt switch such as `DONKEY_TELEMETRY=flase` is an error, not `false`:

```text
ConfigError: Configuration is invalid:
  - timeout_s is 'abc', set in the environment (DONKEY_TIMEOUT_S); expected a number
  - telemetry is 'flase', set in the environment (DONKEY_TELEMETRY); expected one of 1, true, yes, on, 0, false, no, off
  - max_retries is -1, set in the environment (DONKEY_MAX_RETRIES); expected a whole number, 0 or more
Fix each value where it is set: in code, an environment variable, or .donkey-kit.toml.
```

The environment-only switches above (`DONKEY_NO_CACHE`,
`DONKEY_TRUST_PROJECT_CONFIG`, `DONKEY_ALLOW_HTTP`) are not checked: any value
other than the ones listed leaves them off.

## Next steps

- [Overview & all settings](https://docs.donkey-kit.dev/reference/configuration.md): every setting, precedence and the trust rules for config files.
- [Auth modes](https://docs.donkey-kit.dev/reference/configuration/auth.md): client ID, your own provider key, JWT model-wallet and bearer tokens.
- [Control plane & credentials](https://docs.donkey-kit.dev/reference/configuration/control-plane.md): the Anypoint control plane and what the SDK sends where.

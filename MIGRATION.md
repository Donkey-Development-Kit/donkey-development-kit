# Migration guide

## Cost-tag headers are opt-in; secrets and blocked values stay out of output

Four changes to what the SDK renders and sends. Only the first changes
anything on the wire.

**Cost-tag request headers are off by default.** The cost tags (`team`,
`project`, `env`, `enduser.id`) used to go out as `X-Anypoint-Cost-*` request
headers on every call. The LLM Gateway reads none of them
(`docs/verified-apis.md` §3), so they are now sent only when you opt in. The
`donkey.cost.*` span attributes are unchanged and still carry every tag, so
dashboards built on spans need no change. If something of your own reads those
headers, turn them back on:

```diff
+ DONKEY_SEND_COST_HEADERS=true
```

or `send_cost_headers = true` in the `[donkey]` table, or
`DonkeyConfig(send_cost_headers=True)`. The `cost_*_header` name overrides
apply as before.

**`PIIDetected` messages no longer contain the flagged values.** `str(exc)`
used to be the gateway's rejection text, which repeats each detected value. It
now names the entity types, their count and their offsets, for example
`… 1 entity (Email at chars 12-32) …`. `.entities` is unchanged. If you parsed
the message, read the new `.gateway_message` attribute (the gateway's text) or
`.response` (the raw body) instead. Both carry the blocked content.

**`repr()` / `str()` of `DonkeyConfig` omit the secrets.** `client_secret`,
`llm_proxy_client_secret` and `llm_proxy_key` no longer appear. Attribute
access and equality are unchanged.

**Printed `connection_kwargs()` show `'***'` for secrets.** Every adapter's
`connection_kwargs()`, ADK's `gemini_connection_kwargs()` and
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

# Migration guide

## 0.1.2 (unreleased)

Changes since `0.1.1` that can affect existing code. 0.1.2 defines the public
API (#927): a name is public when you can import it from `donkey_kit` or from
a public module's `__all__`; submodule paths are not API. It also deletes the
refused provisioning control plane (#730).

- Sections 1 to 3 break code that used the deleted control plane.
- Sections 4 to 6 still work but emit a `DeprecationWarning`.
- Sections 7 and 8 break code that used removed or internal names.
- Sections 9 and 10 change what `pip` installs.
- Section 11 renames a conformance-plugin option (the old one still works and
  warns).
- Sections 12 to 17 change runtime behaviour without an import error.

The deprecated names stay for the rest of 0.1.x. The release that removes them
says so here and in its Release notes' Breaking changes section. To find every
call site now, run your tests with `-W error::DeprecationWarning`.

### 1. `donkey_kit.provisioning` and `donkey_kit.governance` are gone

**Who:** code that imported `donkey_kit.provisioning` (`spec`, `planner`,
`applier`, `lint`, `publish`, `cli`, `doctor`) or `donkey_kit.governance`
(`Governance`, `GatewayTarget`, `PolicyBinding`) (#730).

**Symptom:** `ModuleNotFoundError: No module named 'donkey_kit.provisioning'`
(or `'donkey_kit.governance'`).

**Fix:** the declarative provisioning control plane is on the build plan's
*Do not build* list, and every verb in it raised `blocked on verification`, so
there is no replacement. Manage gateways and policies with API Manager or
Terraform. The reasoning is ADR 0008 in [`docs/adr/`](docs/adr/) (legacy
quarantine and the CLI's home). A few pieces moved instead of going away:

| Was | Now |
| --- | --- |
| `donkey_kit.provisioning.cli` (`app`, `main`) | `donkey_kit.cli` |
| `donkey_kit.provisioning.doctor` | `donkey_kit.cli.doctor` |
| `donkey_kit.provisioning.doctor.Check` | `donkey_kit.cli.doctor.DoctorCheck` |
| `donkey_kit.provisioning.publish.content_digest` / `publish_if_changed` | `donkey_kit.registry.publication` |

The `[targets.*]` tables in `.donkey-kit.toml` and the `DONKEY_TARGET`
variable were read only by `GatewayTarget.from_env()`. They are now ignored.

The `donkey` commands you can see in `donkey --help` are unchanged.

### 2. Hidden CLI commands `validate`, `plan`, `apply`, `drift`, `lint`, `generate` are removed

**Who:** scripts that ran one of them. All of them except `validate` already
exited 3 with `blocked on verification`.

**Symptom:** `No such command 'plan'.` and exit status 2.

**Fix:** remove the call. `donkey status`, `donkey publish` and `donkey verify`
are still there, hidden, and still exit 3 until Exchange publication (BG §2.5)
is verified.

### 3. Three errors are removed

`ProvisioningError`, `GovernanceDrift` and `PlatformTeamOnly` were raised only
by the deleted modules. Drop them from `except` clauses and `simulate(...)`
calls. `DonkeyError` still catches everything the SDK raises.

### 4. Blocked-surface types moved to `donkey_kit.experimental`

**Who:** code that imported `STRICT`, `AssetRef`, `AssetType`, `Contact`,
`GovernanceCriteria`, `Publication`, `PublicationAssetType`,
`PublicationDrift`, `RegistryError` or `ToolInvocationError` from `donkey_kit`
(#730).

**Symptom:** `DeprecationWarning: donkey_kit.AssetRef is deprecated; import it
from donkey_kit.experimental`. The old spelling still works for now, but the
names are out of `donkey_kit.__all__` (so `from donkey_kit import *` no longer
brings them) and type checkers flag the old import. A later release removes the
alias.

**Fix:**

```diff
- from donkey_kit import STRICT, AssetRef, RegistryError
+ from donkey_kit.experimental import STRICT, AssetRef, RegistryError
```

Every surface these types serve (registry discovery and governed-state checks,
publication, MCP tool calls) still raises `blocked on verification`. They move
back to `donkey_kit` when their surface is verified, and until then they may
change in any release. The submodule paths (`donkey_kit.registry`,
`donkey_kit.core.errors`) still work.

### 5. Two renamed names, with deprecated aliases

**Who:** code that imports any name in the left column (#927).

**Symptom:** the import still works but emits a `DeprecationWarning` naming
the replacement.

| Was | Now | Why |
| --- | --- | --- |
| `donkey_kit.registry.governance` | `donkey_kit.registry.criteria`, or import the names from `donkey_kit.registry` | It shared a name with the old top-level `donkey_kit.governance`. |
| `donkey_kit.simulator.scenarios.Scenario` | `donkey_kit.simulator.scenarios.FaultScenario` | It clashed with `donkey_kit.conformance.Scenario`. |

**Fix:**

```diff
- from donkey_kit.registry.governance import GovernanceCriteria, STRICT
+ from donkey_kit.experimental import GovernanceCriteria, STRICT
- from donkey_kit.simulator.scenarios import Scenario
+ from donkey_kit.simulator.scenarios import FaultScenario
```

### 6. `run_context()` is deprecated

**Who:** code that calls `Donkey.run_context()` or
`donkey_kit.core.run_context()` (#926).

**Symptom:** both still bind a correlation ID for the block, and both emit a
`DeprecationWarning`.

**Fix:**

```diff
- with donkey.run_context("order-42"):
+ with donkey.run(id="order-42"):
```

Without a `Donkey` instance, use `donkey_kit.core.telemetry.run_scope()`.

### 7. Removed names

These are gone, with no alias (#926). Each was unused or had no working caller.

- **The `governance=` keyword of `donkey.tools.discover()`.** `discover()`
  still raises blocked-on-verification, so no working call passed it. Drop the
  argument.
- **`donkey_kit.core.telemetry.span()`** and the span-name constants
  **`SPAN_REGISTRY_RESOLVE`**, **`SPAN_TOOL_CALL`** and
  **`SPAN_PROVISION_APPLY`**. Nothing in the SDK emitted those spans. If you
  opened spans with `span()`, use your OpenTelemetry tracer directly.
- **The class attributes behind `Adapter.extra`.** `extra` is now a read-only
  property that reads the adapter roster. Reading it works as before;
  assigning it raises `AttributeError`.

### 8. Internal names renamed without an alias

These were never public. They are listed only because the old names were
importable in 0.1.1 (#927, #728, #726).

| Was | Now |
| --- | --- |
| `donkey_kit.core.config._TOML_NAME` / `_LOCAL_TOML_NAME` | `TOML_NAME` / `LOCAL_TOML_NAME` |
| `donkey_kit.simulator.inject._resolve` | `resolve_fixture` |
| `donkey_kit.conformance.harness._offline_config` | `offline_config` |
| `DonkeyAsyncClient._swap_transport()` / `DonkeyClient._swap_transport()` | `client.governed_transport.replace_inner()` |
| `donkey_kit.integrations._httpx2_bridge` | `donkey_kit.core.transport.httpx2` |
| `DonkeyAsyncClient._on_request()` (a no-op hook) | removed; the per-send `_inject_headers` event hook does request-side work |
| `Adapter.factory_observes_last_call` | `Adapter.capabilities(factory).observes_last_call` |

### 9. pydantic and pyyaml are no longer installed for you

**Who:** code that imports `pydantic` or `yaml` and relied on `donkey-kit` (or
`donkey-kit[cli]`) to install it (#730).

**Symptom:** `ModuleNotFoundError: No module named 'pydantic'` (or `'yaml'`)
in your own code after upgrading.

**Fix:** depend on them directly. The SDK itself imports neither. Framework
extras that need pydantic (LangChain, ADK, the OpenAI SDK) still bring it in.

### 10. The `dev`, `mcp` and `a2a` extras are gone, and `[all]` installs no test runner

**Who:** anyone who installs `donkey-kit[dev]`, `donkey-kit[mcp]`,
`donkey-kit[a2a]`, or relies on `donkey-kit[all]` to bring in pytest (#744).

**Symptom:** pip warns `donkey-kit 0.1.2 does not provide the extra 'mcp'`
(or `'a2a'`, `'dev'`) and installs nothing for it. After `pip install
"donkey-kit[all]"`, `pytest --donkey-conformance` is not available.

- `mcp` and `a2a` had no code behind them: nothing in the SDK imported `mcp`
  or `a2a-sdk`. Each comes back with the feature that uses it.
- `dev` was contributor tooling (mypy, ruff, import-linter). It is now a
  PEP 735 dependency group in a source checkout and is not published.
- `[all]` is now everything a user runs: `llm`, `langgraph`, `otel`, `cli`
  and `local`. The conformance plugin stays in `[test]`.

**Fix:**

```diff
- pip install "donkey-kit[all]"
+ pip install "donkey-kit[all,test]"     # if you run the conformance plugin
- pip install -e ".[dev,llm,cli]"        # contributors, from python/
+ pip install -e ".[llm,cli]" --group dev
```

Drop `mcp` and `a2a` from any install line. `--group` needs pip 25.1 or later.

### 11. The conformance plugin's `--agent` is now `--donkey-agent`

```diff
- pytest --donkey-conformance --agent=myagent:build
+ pytest --donkey-conformance --donkey-agent=myagent:build
```

The plugin loads on every pytest run, and pytest refuses to start when two
plugins register the same option, so all its options now start with
`--donkey-` (#746). `--agent` still works and emits a `DeprecationWarning`,
unless another plugin registered `--agent` first. `donkey test --agent` is
unchanged; it now forwards `--donkey-agent` to pytest.

### 12. `Donkey()` no longer installs the global OpenTelemetry provider

Reference: [DDK leaves the global provider to you](website/content/telemetry.mdx#ddk-leaves-the-global-provider-to-you).

**Who:** anyone who sets `OTEL_EXPORTER_OTLP_ENDPOINT`, doesn't configure an
OpenTelemetry `TracerProvider` of their own, and relied on `Donkey()` making
its provider the global one (#732). For example, spans from other libraries
reached the collector only because DDK had installed its exporter globally.

**Symptom:** DDK's own spans still reach the collector, but
`trace.get_tracer_provider()` is unchanged after `Donkey()`, so spans that
other code creates through the global provider are no longer exported. A
`trace.get_tracer_provider().force_flush()` call no longer flushes DDK's spans
either; they are flushed when the interpreter exits.

**Fix:** set `DONKEY_TELEMETRY_INSTALL_GLOBAL=true` (or
`telemetry_install_global = true` in `.donkey-kit.toml`) to get the old
behaviour, or configure your own `TracerProvider`. DDK's spans go to a provider
you set even if you set it after `Donkey()`. ADR 0010 in
[`docs/adr/`](docs/adr/) has the reasoning.

### 13. The user config file is merged beneath the project files

**Who:** anyone with a user file (`$XDG_CONFIG_HOME/.donkey-kit.toml`, or
`~/.config/.donkey-kit.toml`) who also has a `.donkey-kit.toml` or
`.donkey-kit.local.toml` in the working directory.

**Symptom:** keys from the user file now apply wherever the working-directory
files leave them unset. Before, the user file was read only when neither
working-directory file existed
([#727](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/727)).
A credential from the user file is still never sent to an `llm_proxy_url` or
`base_url` read from the working-directory files, so a project file that names
a URL but no credentials now raises the endpoint `ConfigError` instead of a
missing-field one.

**Fix:** remove from the user file any key you don't want applied to every
project, or set it in the project's own files. `donkey doctor` labels values
from it `user file`.

### 14. `Donkey.from_env()` accepts every field

`Donkey.from_env(...)` and the new `DonkeyConfig.resolve(...)` accept any
`DonkeyConfig` field as a keyword argument, plus `path=` to read a named
config file in place of `./.donkey-kit.toml` (#727). An unknown name raises
`TypeError`. `on_model_substitution=None` is no longer accepted; leave the
argument out instead.

### 15. A 502 or 504 on a model call is no longer retried by default

**Who:** code that relies on the transport re-sending a model call (a `POST`
whose body, or native Gemini path, names a model) after a `502` or `504`
(#728).

**Symptom:** the `502` or `504` comes back on the first attempt, as an
`UpstreamModelError` once classified. Either status can follow an upstream
call that already completed and was billed, so a re-send could bill twice, and
the gateway has no verified idempotency key (ADR 0009 in
[`docs/adr/`](docs/adr/)). A `503`, and every request that is not a model
call, is still retried as before.

**Fix:** if a second charge is acceptable, opt back in with
`DONKEY_RETRY_MODEL_CALLS_ON_GATEWAY_ERRORS=1` in the environment, or set
`retry_model_calls_on_gateway_errors` in code:

```python
from donkey_kit import Donkey

donkey = Donkey.from_env(retry_model_calls_on_gateway_errors=True)
```

### 16. Errors leaving `donkey.run()` and `@donkey.governed` are typed

**Who:** code that catches a framework's or HTTP SDK's own error (for example
`openai.APIStatusError`) around a block wrapped in `donkey.run()` or a
function decorated with `@donkey.governed` (#724).

**Symptom:** a gateway refusal now leaves the block as the SDK's typed error,
such as `PIIDetected`, instead of the framework's exception. The framework's
error is on `exc.framework_error`. Errors that are not governed refusals pass
through unchanged.

**Fix:** catch the typed error (or `DonkeyError`), or pass
`typed_refusals=False` to `donkey.run()` or `@donkey.governed` to get the
framework's errors as before. See
[Typed refusals at the framework boundary](website/content/errors.mdx#typed-refusals-at-the-framework-boundary).

### 17. A request-rate-limit 429 is `RequestRateLimitExceeded`

**Who:** code that catches `TokenBudgetExceeded` to handle every `429` from a
proxy, or that checks `exc.policy == "token-rate-limit"` (#974).

**Symptom:** on a proxy with the stock `rate-limiting` policy (request count,
`exposeHeaders: true`), the `429` now raises `RequestRateLimitExceeded`. It is
a sibling of `TokenBudgetExceeded`, not a subclass, so an `except
TokenBudgetExceeded:` block no longer catches it. Its `policy` is
`"request-rate-limit"`, and its `retry_after` comes from `x-ratelimit-reset`.
The telemetry attribute `donkey.policy.type` reads `request_rate_limit`. A
token-rate-limit `429`, and any `429` without the `x-ratelimit-*` headers, is
still `TokenBudgetExceeded`.

**Fix:** catch both, or catch their common base `PolicyViolation`:

```python
from donkey_kit import RequestRateLimitExceeded, TokenBudgetExceeded

try:
    ...
except (TokenBudgetExceeded, RequestRateLimitExceeded) as exc:
    wait = exc.retry_after
```

`donkey.budget.pace()` now also refuses when the request window is spent, and
`BudgetReserveReached.window` says which window it was (`"tokens"` or
`"requests"`). Pass it to `donkey.budget.wait_for_reset(window=exc.window)`.

### Shipped simulator fixtures moved into the package (not breaking)

The captured gateway responses that `simulate()`, `donkey mock` and the
`gateway` fixture replay now ship inside the wheel, under
`donkey_kit/simulator/_fixtures/`, instead of being copied in from
`tests/fixtures/` at build time (#746). Nothing changes for installed
code. If you read those files from a source checkout, use the new path.

### New exports (not breaking)

The types that public `Donkey` members return can now be imported from
`donkey_kit`: `LastCall`, `LastCallStatus`, `RunScope`, `ToolsFacade` (was the
private `_ToolsFacade`), `CacheScope`, `DonkeyAsyncClientView`,
`DonkeyClientView`, `LLMClient`, `ExchangeRegistry` and `ConfigOverrides`.
Import them from `donkey_kit` rather than from their submodules. The
typed-refusal bridge is new as `donkey_kit.typed_refusals` and
`donkey_kit.TypedRefusals` (#724), and `donkey_kit.integrations` exports
`AdapterProtocol` and `AdapterCapabilities` (#726). `RequestRateLimitExceeded`
and `RequestWindow` (the type of `donkey.budget.requests`) are new in
`donkey_kit` (#974).

### LlamaIndex, Agent Framework and ADK `model()` report `last_call` (not breaking)

`donkey.last_call` on a `Donkey` used only through LlamaIndex, Microsoft Agent
Framework or ADK's `model()` now reads `UNOBSERVED` before a call and
`OBSERVED` after it, instead of `UNAVAILABLE`. Code that branched on
`UNAVAILABLE` for these adapters no longer sees it. Only CrewAI still reports
`UNAVAILABLE`.

`donkey.llamaindex.typed_refusals()` is new. It turns the `openai`
`APIStatusError` that LlamaIndex raises on a proxy refusal into the SDK's typed
error, such as `PIIDetected`, and keeps the original on `.framework_error`
([#740](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/740)).

## 0.1.1: credential handling, endpoint trust and printed output

Changes since `0.1.0` that can affect existing code, grouped by what you
would notice. Sections 1 to 11 change what the SDK sends or accepts; sections
12 to 15 change only what it prints or how errors are chained; sections 16 and
17 change how a refusal is typed and what `donkey-kit[all]` installs. The
reference for each rule is linked from its section.

### 1. Non-loopback `http://` endpoints are refused

**Who:** anyone whose `llm_proxy_url` / `DONKEY_LLM_PROXY_URL`, `base_url` /
`ANYPOINT_BASE_URL` or connected-app token endpoint uses `http://` on a host
other than `localhost`, `127.0.0.0/8` or `::1`. This applies wherever the URL is
set: environment, file or code. It includes a simulator reached from another
machine or container (`http://<lan-ip>:8080`, `http://simulator:8080`,
`http://0.0.0.0:8080`).

**Symptom:** building a client raises:

```text
ConfigError: llm_proxy_url must be an https:// URL (got http scheme, llm.example.test). Plain http:// is accepted for loopback hosts (localhost, 127.0.0.0/8, ::1), such as the local gateway simulator. Change it to the https:// address of the service, or set DONKEY_ALLOW_HTTP=1 in the environment to allow plain http:// to other hosts.
```

**Fix:** use the service's `https://` address. For the simulator, run the
client on the same host and use `http://127.0.0.1:<port>`. If the endpoint is
on a network you control and has no TLS (a simulator in another container, an
in-cluster proxy), set `DONKEY_ALLOW_HTTP=1` (or `true`, `yes`, `on`) in the
environment. It covers `base_url`, `llm_proxy_url` and the token endpoint.
Credentials then travel unencrypted to that host, and the SDK emits a
`ConfigWarning` naming the key and the host, and quoting the value as you set
it:

```text
ConfigWarning: llm_proxy_url uses plain http:// to llm.example.test because DONKEY_ALLOW_HTTP=1 is set. Credentials and data sent to it are not encrypted in transit.
```

`DONKEY_ALLOW_HTTP` is read from the environment only; a config file can't set
it. It applies to `http://` URLs with a host only (other schemes are still
refused), and it doesn't relax the credential rule in section 2.

### 2. A URL from the project's config files only gets credentials from those files

Reference: [Which credentials a URL receives](website/content/reference/configuration.mdx#which-credentials-a-url-receives).

**Who:** anyone with `llm_proxy_url` or a non-standard `base_url` in
`./.donkey-kit.toml` or `./.donkey-kit.local.toml`, while a credential for that
endpoint comes from somewhere else. Loopback URLs are included. The usual cases:

- The URL is in `.donkey-kit.toml` and the secret is in an environment
  variable. This is the split `donkey init` produced before this release.
- The URL is in `.donkey-kit.toml` and the secret is set in code, for example
  `with_overrides(llm_proxy_client_secret=vault.read(...))` or the same through
  `dataclasses.replace(...)`.
- A custom `base_url` in `.donkey-kit.toml` with a `Donkey(auth=…)` provider.
  Its token never comes from a file. This one fails only at the first
  control-plane call.
- `jwt` mode with `llm_proxy_url` in either file. The JWT from `llm_auth`
  never comes from a file, so this case always fails.
- A `base_url` on a host other than the four standard Anypoint hosts in
  `.donkey-kit.toml`, with `ANYPOINT_CLIENT_SECRET` in the environment. This
  one fails only at the first control-plane call.

**Not affected:** URL and credentials all in environment variables (the usual
CI setup); config built entirely with `DonkeyConfig(...)`; URLs in the user
config file; a `base_url` on `anypoint.mulesoft.com`, `eu1.`, `ca1.` or `jp1.`.

**Symptom:** building a client (`donkey.llm.client()`, a framework factory,
`connection_kwargs()`) raises, before anything is sent:

```text
ConfigError: Not sending llm_proxy_client_secret (from env) to llm-proxy.example.com: llm_proxy_url is set in /home/me/my-agent/.donkey-kit.toml, and credentials from outside the project config files are only sent to hosts those files name when you opt in. To continue, do one of:
  - set the URL in the environment instead (DONKEY_LLM_PROXY_URL=https://...)
  - keep the credentials in /home/me/my-agent/.donkey-kit.local.toml, next to the project file
  - trust the project config files by setting DONKEY_TRUST_PROJECT_CONFIG=1
```

`donkey doctor` shows the same message on its `config` line (for `llm_proxy_url`) or its `control plane` line (for `base_url`).

**Fix:** pick one.

- **Move the secret next to the file** (local development; not possible in
  `jwt` mode):

  ```diff
    # .donkey-kit.toml (committed)
    [donkey]
    llm_proxy_url = "https://llm-proxy.example.com/my-proxy/"
    llm_proxy_client_id = "my-client-id"
  ```

  ```diff
  - export DONKEY_LLM_PROXY_CLIENT_SECRET="<secret>"
  + # .donkey-kit.local.toml (gitignored)
  + [donkey]
  + llm_proxy_client_secret = "<secret>"
  ```

- **Set the URL in the environment too** (CI, `jwt` mode, secrets set in
  code). The environment wins over the file:

  ```diff
    export DONKEY_LLM_PROXY_CLIENT_SECRET="<secret>"
  + export DONKEY_LLM_PROXY_URL="https://llm-proxy.example.com/my-proxy/"
  ```

- **Opt in** if you trust the directory's config files:
  `export DONKEY_TRUST_PROJECT_CONFIG=1`. It is read from the environment only;
  a config file can't set it.

A value you change on a resolved config counts as set in code (labelled `code`
in the error), however you change it: `with_overrides(...)`,
`dataclasses.replace(...)` or any other copy. A copy that keeps the value keeps
its source, including `DonkeyConfig(**dataclasses.asdict(cfg))` in the same
process. A config rebuilt in another process keeps where its URLs came from,
but its credentials count as set in code. So a URL you override in code is no
longer bound to the file's credentials, and a secret you override in code no
longer passes as a file credential.

This matters if you hand a config to a worker in another process
(`multiprocessing` with the spawn start method, Celery, Ray). A worker that gets
a URL from `.donkey-kit.toml` and credentials from `.donkey-kit.local.toml`
refuses to send them. Call `DonkeyConfig.from_env()` in the worker instead, or
set the URL in the environment.

### 3. `.donkey-kit.local.toml` is now read

**Who:** anyone with a `.donkey-kit.local.toml` in the working directory. The
previous `donkey init` template suggested creating one, but the SDK didn't read
it.

**Symptom:** its values now apply. They merge into `.donkey-kit.toml` key by
key, including nested tables such as `[donkey.cost]`; a scalar or array in the
local file replaces the project file's value. Environment variables still win
over both. While it exists, the user file `$XDG_CONFIG_HOME/.donkey-kit.toml`
isn't read, even without a `.donkey-kit.toml`
([#727](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/727)).
`donkey doctor` labels values from it `local overlay`.

**Fix:** review the file, and delete keys you don't want applied. Make sure it
is gitignored.

A secret (`client_secret`, `llm_proxy_client_secret`, `llm_proxy_key`) in the
committed `.donkey-kit.toml` now emits a `ConfigWarning`
(`donkey_kit.core.errors.ConfigWarning`). Under `-W error` or
pytest `filterwarnings = error` that warning fails the run; move the secret to
`.donkey-kit.local.toml`.

### 4. Each credential stays on its own plane

Reference: [What the SDK sends where](website/content/reference/configuration.mdx#what-the-sdk-sends-where).

`Donkey` now keeps one HTTP client for the LLM proxy and one for the Anypoint
platform. Model calls never request, send or refresh the connected-app token,
so they keep working when the Anypoint token endpoint is unreachable. The
features that use the control plane (registry, tool discovery, publication,
governance, provisioning) are still on the roadmap, so in this release the
token endpoint is contacted only if your own code calls
`AnypointConnectedApp.token()`. See
[Which features use the control plane](website/content/reference/configuration.mdx#which-features-use-the-control-plane).

**4a. `Donkey(auth=…)` no longer reaches the LLM proxy.**

*Who:* callers whose LLM proxy authenticated model calls with the bearer token
from `Donkey(auth=…)`. The token went out only on requests whose client set no
`Authorization` of its own: the Anthropic client, ADK's `gemini()`, and raw
requests through the shared client. OpenAI-compatible clients were already
sending their API-key slot instead.

*Symptom:* the first model call raises `AuthError` (a `401` from the proxy),
with no retry.

*Fix:* supply the token on the data plane.

- OpenAI-compatible clients, static key: set `llm_proxy_key` /
  `DONKEY_LLM_PROXY_KEY`. It is sent as `Authorization: Bearer <key>`. (The
  Anthropic client sends it as `x-api-key`, and ADK's `gemini()` as
  `x-goog-api-key`.)
- Any client with transport injection, rotating token: use `jwt` mode. This
  requires `llm_proxy_wallet_client_id` (sent as `X-Client-Id`) and is
  async-only:

  ```diff
  - donkey = Donkey(cfg, auth=StaticToken(token))
  + donkey = Donkey(
  +     replace(cfg, llm_proxy_auth="jwt", llm_proxy_wallet_client_id="my-wallet-id"),
  +     llm_auth=StaticToken(token),
  + )
  ```

  (`cfg.with_overrides(...)` works the same way.)
- Any client with transport injection, rotating bearer token, no wallet: use
  `bearer` mode. Only `llm_proxy_url` is required; the token from `llm_auth` is
  sent as `Authorization: Bearer <token>` with no `X-Client-Id` and no
  `client_id`/`client_secret` pair, refreshed and re-sent once on a `401`, and
  covered by the endpoint check. Like `jwt` mode it is async-only, and CrewAI,
  which builds its own HTTP client, raises `ConfigError` in this mode:

  ```diff
  - donkey = Donkey(cfg, auth=StaticToken(token))
  + donkey = Donkey(replace(cfg, llm_proxy_auth="bearer"), llm_auth=StaticToken(token))
  ```

  This covers the Anthropic client and ADK's `gemini()`, which have no
  OpenAI-style API-key slot.

**4b. In client-id mode, a `401` from the LLM proxy is no longer retried.**

*Who:* client-id mode with `ANYPOINT_CLIENT_ID` / `ANYPOINT_CLIENT_SECRET` set.
*Symptom:* the same `AuthError` as before, one request sooner. The old retry
refreshed a token the proxy doesn't check, so it could never succeed. *Fix:*
none needed. If you see the error, check `DONKEY_LLM_PROXY_CLIENT_ID` /
`DONKEY_LLM_PROXY_CLIENT_SECRET` and the contract in API Manager. In `jwt` mode
the wallet JWT is still refreshed and retried once.

**4c. In `jwt` mode, control-plane calls carry the connected-app token.** They
no longer carry the wallet JWT or `X-Client-Id`, so registry and tool discovery
need `ANYPOINT_CLIENT_ID` / `ANYPOINT_CLIENT_SECRET` (or your own
`Donkey(auth=…)`) in `jwt` mode too. Those features still raise
`NotImplementedError` before sending anything, so nothing changes on the wire
in this release.

`simulate()`, `donkey mock` and the conformance kit still act on the LLM-proxy
client, and `aclose()` closes both clients.

### 5. Cost-tag headers are off by default

Reference: [Cost-attribution tags](website/content/telemetry.mdx#cost-attribution-tags).

**Who:** anything of yours that reads `X-Anypoint-Cost-*` request headers. The
LLM Gateway reads none of them (`docs/verified-apis.md` §3).

**Symptom:** the headers are no longer sent. The `donkey.cost.*` span
attributes are unchanged, so dashboards built on spans need no change.

**Fix:** turn them back on with any one of these:

```diff
+ DONKEY_SEND_COST_HEADERS=true
```

```toml
[donkey]
send_cost_headers = true
```

```python
cfg = DonkeyConfig.from_env().with_overrides(send_cost_headers=True)
```

When they are on, the headers, including `X-Anypoint-Cost-Enduser-Id`, go only
on model requests to the LLM proxy. Anypoint control-plane requests and the
connected-app token request carry none of them, nor the attribution or
`x-cache-*` headers
([#833](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/833)).
The `cost_*_header` name overrides apply as before.

### 6. `donkey doctor` output

`doctor` now prints `llm endpoint` and `control plane` lines (host and where the
value came from) before the probe. It reports an endpoint error on the `config`
line and skips the probe. With `DONKEY_ALLOW_HTTP=1` set it also shows:

```text
[i]  plain http     allowed to non-loopback hosts (DONKEY_ALLOW_HTTP=1 in env)
```

If you parse `donkey doctor --json`, expect the new entries named `llm endpoint`, `control plane` and, when the switch is on, `plain http`.

### 7. Some configurable header names are refused

Reference: [Header names you can't use](website/content/reference/configuration.mdx#header-names-you-cant-use).

**Who:** anyone who sets `correlation_header`, `call_id_header` or a
`cost_*_header` key (in the environment, a file or code) to a name that:

- doesn't start with `X-` (for example `Team` or `Origin`);
- routes or frames the request (`Host`, `X-Forwarded-*`, `Content-Length`, …);
- can change the request's method or path (`X-HTTP-Method-Override`,
  `X-Original-URL`, `X-Rewrite-URL`, …);
- carries credentials (`Authorization`, `Cookie`, `x-api-key`, `client_secret`, …);
- is already set by the SDK or a framework's SDK (`Content-Type`, `User-Agent`,
  the attribution, `x-cache-*` and `x-stainless-*` headers, …);
- is already used by another of these keys;
- or isn't a valid header name.

Names compare case-insensitively. All the default names start with `X-`.

**Symptom:** building the config raises, whichever way it is built:

```text
ConfigError: cost_team_header names the header 'Host', which can't be used: it controls where the request goes or how it is framed. cost_team_header is set in the environment (DONKEY_COST_TEAM_HEADER). Choose a different header name.
```

**Fix:** choose a different header name that starts with `X-`.

### 8. Config files that link outside the working directory are refused

**Who:** anyone whose `.donkey-kit.toml` or `.donkey-kit.local.toml` is a link
to a file outside the working directory.

**Symptom:** loading the config raises:

```text
ConfigError: /home/me/my-agent/.donkey-kit.toml links to /home/me/shared/donkey.toml, outside the working directory. Replace the link with a regular file in the working directory, or set those values in the environment.
```

**Fix:** replace the link with a regular file (a link to a file inside the
working directory is fine), or set the values in the environment.

### 9. URL overrides passed to factories must use `https://`

Reference: [Credentials go only to checked endpoints](website/content/reference/configuration.mdx#credentials-go-only-to-checked-endpoints).

**Who:** code that passes a URL to a factory: `base_url` to
`donkey.llm.client()` or an adapter factory, `api_base` (LlamaIndex, ADK's
`model()`, CrewAI), `openai_api_base` (LangGraph) or Strands'
`client_args["base_url"]`.

**Symptom:** a non-loopback `http://` URL, another scheme, or a URL without a
host raises the same `ConfigError` as in section 1, naming the keyword (for
example `base_url must be an https:// URL …`).

**Fix:** use the `https://` address, or set `DONKEY_ALLOW_HTTP=1` as in
section 1. An accepted override receives the configured credentials, as before.

### 10. Credentials go only to checked endpoints

**Who:** code that sends an SDK-built client somewhere other than the
configured proxy or a URL passed to a factory, for example by changing
`base_url` after the fact (`ChatOpenAI(**{**kwargs, "base_url": other})`), or
that follows redirects across origins.

**Symptom:** the request is still sent, but without credentials: no
`client_id` / `client_secret`, `Authorization`, `x-api-key` or `X-Client-Id`,
including credential headers the framework set itself. The proxy at the other
address answers `401`. The SDK's clients don't follow redirects; a redirect hop
your code follows to another origin also carries no credentials. CrewAI gets
the same rule through an `interceptor` in its `connection_kwargs()`.

**Fix:** pass the URL to the factory instead (section 9), or set it as
`llm_proxy_url`.

### 11. LlamaIndex, MS Agent Framework and ADK's `model()` send through the SDK's client

Reference: [Injection depth](website/content/frameworks/index.mdx#injection-depth-differs-by-framework).

**Who:** users of these adapters, and anyone who spreads `connection_kwargs()`
into a constructor.

**Symptom:** `connection_kwargs()` has new keys when the framework's
dependencies are installed:

| Adapter | New keys |
|---|---|
| LangGraph | `http_client` (the blocking client, used by `invoke()`) |
| LlamaIndex | `http_client`, `async_http_client` |
| MS Agent Framework | `async_client` (an `AsyncOpenAI` on the SDK's client) |
| ADK `model()` | `client` (an `AsyncOpenAI` on the SDK's client) |
| CrewAI | `interceptor` |

Calls through LlamaIndex, MS Agent Framework and ADK's `model()` now carry the
run's correlation ID, get the SDK's retries and spans, populate
`donkey.last_call` in the context that made the call, and carry the JWT in
`jwt` mode (async calls only). `donkey.last_call` on a `Donkey` that resolved
only these adapters reads `UNOBSERVED` before a call and `OBSERVED` after it
([#740](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/740)).

**Fix:** none needed if you spread `connection_kwargs()` whole. If you pick
keys out of it, add the new ones. If you replace one (for example your own
`http_client`), that client doesn't get the SDK's headers, retries or checked
endpoints.

### 12. `PIIDetected` messages no longer contain the flagged values

**Who:** code that parses `str(exc)`. **Symptom:** the message now names the
entity types, their count and their offsets, for example
`… 1 entity (Email at chars 12-32) …`. **Fix:** read `.gateway_message` (the
gateway's text) or `.response` (the raw body); both contain the flagged values.
`.entities` is unchanged.

### 13. `repr()` / `str()` of `DonkeyConfig` omit the secrets

`client_secret`, `llm_proxy_client_secret` and `llm_proxy_key` no longer appear.
Attribute access and equality are unchanged.

### 14. Printed `connection_kwargs()` show `'***'` for secrets

**Who:** tests that compare printed output. **Fix:** compare the mapping
instead. Unpacking, lookups, equality and `json.dumps` are unchanged (and
`json.dumps` writes the real values). `dict(kwargs)`, `{**kwargs}` and
`kwargs.items()` still show the top-level `api_key`, and some framework objects
built from the kwargs print credentials in their own `repr()`: LangGraph's
`ChatOpenAI` (`client_secret`), LlamaIndex's `OpenAILike` (`client_secret` and
`api_key`) and CrewAI's `OpenAICompletion` (`api_key`). Details:
[What printed output hides](website/content/reference/configuration.mdx#what-printed-output-hides).

### 15. `typed_refusals()` no longer chains the LangChain error

**Who:** code that reads `__cause__` on an error raised by LangGraph's
`typed_refusals()`.

**Symptom:** `exc.__cause__` is `None`. The LangChain error repeats the
gateway's rejection text (for a PII block, the flagged values), and a traceback
prints every chained exception.

`typed_refusals()` is now a class-based context manager, so no frame in the
error's traceback holds the LangChain error as a local variable either. Error
reporters that print frame locals (Sentry, `pytest -l`) don't show it. The
error itself is still on `exc.framework_error`, and Python keeps it on the
suppressed `exc.__context__`; both hold the gateway's text.

**Fix:** read `exc.framework_error` for the original LangChain error. See
[Refusal messages don't repeat blocked content](website/content/errors.mdx#refusal-messages-dont-repeat-blocked-content).

### 16. An Agent Kill Switch block raises `AgentKilled`

Reference: [The rejection shapes `classify()` types](website/content/errors.mdx#the-rejection-shapes-classify-types).

**Who:** code that catches the 403 the gateway returns when the Agent Kill
Switch has blocked the agent.

**Symptom:** that 403 (nested error `code` `agent_killed`) now raises
`AgentKilled`, a `PolicyViolation`. In 0.1.0 it raised `UpstreamRequestError`,
so an `except UpstreamRequestError` no longer catches it. It is still terminal
and not retried.

**Fix:** catch `AgentKilled` (or `PolicyViolation`), and ask an administrator to
restore the agent's model access in Governance > Security.

### 17. `donkey-kit[all]` leaves out the seven `connection_kwargs()` frameworks

**Who:** anyone who installs `donkey-kit[all]` to get a framework other than
LangGraph.

**Symptom:** `all` now installs `llm`, `langgraph`, `mcp`, `otel`, `cli`,
`local` and `test` only. In 0.1.0 it also listed `adk`, `strands`,
`agent_framework`, `openai-agents`, `anthropic`, `crewai` and `llamaindex`,
whose current releases can't be installed together, so pip failed with
`resolution-too-deep`.

**Fix:** add the framework you use: `pip install "donkey-kit[all,crewai]"`.

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

The top-level command is now `donkey`. `donkey --help` lists four commands:

```bash
donkey init      # write a commented .donkey-kit.toml from the resolved config
donkey doctor    # diagnose config, credentials, gateway, model and budget
donkey mock      # run the local gateway simulator
donkey test --agent=myagent:build   # run the conformance suite against your agent
```

The provisioning commands (`validate`, `plan`, `apply`, `drift`, `lint`,
`generate`, `status`, `publish`, `verify`) are hidden from `--help`. All of
them except `validate` exit with status 3 and a `blocked on verification`
message. (A later release removed all but `status`, `publish` and `verify`; see
the first section of this guide.)

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
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, …) are untouched —
they are not ours to rename. A governed model call still opens **one** span
carrying both namespaces at once.

Span name (the SDK emits one span, for each governed model call):

| Was | Now |
| --- | --- |
| `fabric.llm.chat` | `donkey.llm.chat` |

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
- The `gen_ai.*` OpenTelemetry attributes and `x-anypoint-*` /
  `x-correlation-id` headers.

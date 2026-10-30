# Migration guide

<!-- toc -->
**Contents**

- [0.2.0: governed tools, publishing, approvals and more typed refusals](#020-governed-tools-publishing-approvals-and-more-typed-refusals)
  - [1. A CIBA approval response is GatewayApprovalPending or GatewayApprovalDenied](#1-a-ciba-approval-response-is-gatewayapprovalpending-or-gatewayapprovaldenied)
  - [2. Google Model Armor and CrowdStrike AIDR blocks are ContentSafetyBlocked](#2-google-model-armor-and-crowdstrike-aidr-blocks-are-contentsafetyblocked)
  - [3. classify is a Classifier object](#3-classify-is-a-classifier-object)
  - [4. donkey publish and donkey verify run, and no longer exit 3](#4-donkey-publish-and-donkey-verify-run-and-no-longer-exit-3)
  - [5. Registry, discovery and ToolSet calls reach Anypoint](#5-registry-discovery-and-toolset-calls-reach-anypoint)
  - [6. donkey.tools.discover() defaults to governed=True](#6-donkeytoolsdiscover-defaults-to-governedtrue)
  - [7. Import GovernanceCriteria from donkey_kit again](#7-import-governancecriteria-from-donkey_kit-again)
  - [8. [cli] needs newer typer and rich, and no longer installs click](#8-cli-needs-newer-typer-and-rich-and-no-longer-installs-click)
  - [9. The mcp and a2a extras are back, and [all] and [adk] install more](#9-the-mcp-and-a2a-extras-are-back-and-all-and-adk-install-more)
  - [10. An AgentKilled stops that Donkey for good](#10-an-agentkilled-stops-that-donkey-for-good)
  - [11. refusal_translator leaves two adapters' __all__](#11-refusal_translator-leaves-two-adapters-__all__)
  - [Budget headers (not breaking)](#budget-headers-not-breaking)
  - [ADK gemini() and model() return subclasses (not breaking)](#adk-gemini-and-model-return-subclasses-not-breaking)
  - [OpenAI Agents SDK is a conformance-tested adapter (not breaking)](#openai-agents-sdk-is-a-conformance-tested-adapter-not-breaking)
  - [New public names (not breaking)](#new-public-names-not-breaking)
  - [If you used a 0.2.0 pre-release](#if-you-used-a-020-pre-release)
- [0.1.3: no breaking changes](#013-no-breaking-changes)
  - [Typed refusals through ADK model() (not breaking)](#typed-refusals-through-adk-model-not-breaking)
  - [CA and JP control-plane hosts (not breaking)](#ca-and-jp-control-plane-hosts-not-breaking)
- [0.1.2: public API surface, removed control plane and typed errors](#012-public-api-surface-removed-control-plane-and-typed-errors)
  - [1. donkey_kit.provisioning and donkey_kit.governance are gone](#1-donkey_kitprovisioning-and-donkey_kitgovernance-are-gone)
  - [2. Hidden CLI commands validate, plan, apply, drift, lint, generate are removed](#2-hidden-cli-commands-validate-plan-apply-drift-lint-generate-are-removed)
  - [3. Three errors are removed](#3-three-errors-are-removed)
  - [4. Blocked-surface types moved to donkey_kit.experimental](#4-blocked-surface-types-moved-to-donkey_kitexperimental)
  - [5. Two renamed names, with deprecated aliases](#5-two-renamed-names-with-deprecated-aliases)
  - [6. run_context() is deprecated](#6-run_context-is-deprecated)
  - [7. Removed names](#7-removed-names)
  - [8. Internal names renamed without an alias](#8-internal-names-renamed-without-an-alias)
  - [9. pydantic and pyyaml are no longer installed for you](#9-pydantic-and-pyyaml-are-no-longer-installed-for-you)
  - [10. The dev, mcp and a2a extras are gone, and [all] installs no test runner](#10-the-dev-mcp-and-a2a-extras-are-gone-and-all-installs-no-test-runner)
  - [11. The conformance plugin's --agent is now --donkey-agent](#11-the-conformance-plugins---agent-is-now---donkey-agent)
  - [12. Donkey() no longer installs the global OpenTelemetry provider](#12-donkey-no-longer-installs-the-global-opentelemetry-provider)
  - [13. The user config file is merged beneath the project files](#13-the-user-config-file-is-merged-beneath-the-project-files)
  - [14. Donkey.from_env() accepts every field](#14-donkeyfrom_env-accepts-every-field)
  - [15. A 502 or 504 on a model call is no longer retried by default](#15-a-502-or-504-on-a-model-call-is-no-longer-retried-by-default)
  - [16. Errors leaving donkey.run() and @donkey.governed are typed](#16-errors-leaving-donkeyrun-and-donkeygoverned-are-typed)
  - [17. A request-rate-limit 429 is RequestRateLimitExceeded](#17-a-request-rate-limit-429-is-requestratelimitexceeded)
  - [Shipped simulator fixtures moved into the package (not breaking)](#shipped-simulator-fixtures-moved-into-the-package-not-breaking)
  - [New exports (not breaking)](#new-exports-not-breaking)
  - [LlamaIndex, Agent Framework and ADK model() report last_call (not breaking)](#llamaindex-agent-framework-and-adk-model-report-last_call-not-breaking)
- [0.1.1: credential handling, endpoint trust and printed output](#011-credential-handling-endpoint-trust-and-printed-output)
  - [1. Non-loopback http:// endpoints are refused](#1-non-loopback-http-endpoints-are-refused)
  - [2. A URL from the project's config files only gets credentials from those files](#2-a-url-from-the-projects-config-files-only-gets-credentials-from-those-files)
  - [3. .donkey-kit.local.toml is now read](#3-donkey-kitlocaltoml-is-now-read)
  - [4. Each credential stays on its own plane](#4-each-credential-stays-on-its-own-plane)
  - [5. Cost-tag headers are off by default](#5-cost-tag-headers-are-off-by-default)
  - [6. donkey doctor output](#6-donkey-doctor-output)
  - [7. Some configurable header names are refused](#7-some-configurable-header-names-are-refused)
  - [8. Config files that link outside the working directory are refused](#8-config-files-that-link-outside-the-working-directory-are-refused)
  - [9. URL overrides passed to factories must use https://](#9-url-overrides-passed-to-factories-must-use-https)
  - [10. Credentials go only to checked endpoints](#10-credentials-go-only-to-checked-endpoints)
  - [11. LlamaIndex, MS Agent Framework and ADK's model() send through the SDK's client](#11-llamaindex-ms-agent-framework-and-adks-model-send-through-the-sdks-client)
  - [12. PIIDetected messages no longer contain the flagged values](#12-piidetected-messages-no-longer-contain-the-flagged-values)
  - [13. repr() / str() of DonkeyConfig omit the secrets](#13-repr--str-of-donkeyconfig-omit-the-secrets)
  - [14. Printed connection_kwargs() show '*' for secrets](#14-printed-connection_kwargs-show--for-secrets)
  - [15. typed_refusals() no longer chains the LangChain error](#15-typed_refusals-no-longer-chains-the-langchain-error)
  - [16. An Agent Kill Switch block raises AgentKilled](#16-an-agent-kill-switch-block-raises-agentkilled)
  - [17. donkey-kit[all] leaves out the seven connection_kwargs() frameworks](#17-donkey-kitall-leaves-out-the-seven-connection_kwargs-frameworks)
<!-- tocstop -->

## 0.2.0: governed tools, publishing, approvals and more typed refusals

Changes since `0.1.3` that can affect existing code. 0.2.0 implements much of
what 0.1.x left `blocked on verification`: governed MCP discovery and binding,
`donkey publish` and `donkey verify`, the A2A server (`donkey.serve()`) and
human approvals. It also gives three more gateway refusals their own types.

- Sections 1 to 3 change what `classify()` returns, or what it is.
- Sections 4 to 6 cover calls that raised `NotImplementedError` in 0.1.3 and
  now run.
- Section 7 still works but emits a `DeprecationWarning`.
- Sections 8 and 9 change what `pip` installs.
- Sections 10 and 11 change runtime behaviour and two adapters' `__all__`.

The names 0.1.2 deprecated (its sections 4 to 6 and 11) still work in 0.2.0
and still warn. The one exception is `GovernanceCriteria`, which is back in
`donkey_kit` without a warning (section 7). The release that removes the
others says so here and in its Release notes' Breaking changes section.

If you wrote code against a 0.2.0 pre-release (`0.2.0.devN` from TestPyPI),
also read [If you used a 0.2.0 pre-release](#if-you-used-a-020-pre-release).

### 1. A CIBA approval response is `GatewayApprovalPending` or `GatewayApprovalDenied`

**Who:** code that calls a proxy whose OAuth 2.0 OBO policy asks the user to
approve a call on another device (CIBA), and catches `AuthError` or reads
`PolicyViolation.policy` (#1320).

**Symptom:** `classify()` checks both shapes before its `401` and `403` rules.

- A `401` whose body is `{"error": "authorization_pending"}` was an
  `AuthError`. It is now `GatewayApprovalPending`, a `DonkeyError` that is
  neither an `AuthError` nor a `PolicyViolation`: nothing has been refused
  yet. Its `retry_after` is the wait the gateway asked for, and the same
  request succeeds once the user approves. An `except AuthError:` block no
  longer catches it.
- A `403` whose body is `{"error": "access_denied"}` was a `PolicyViolation`
  with `policy == "unknown"`, or an `AuthError` when it carried a
  `WWW-Authenticate` header. It is now `GatewayApprovalDenied`, a
  `PolicyViolation` with `policy == "oauth2-obo-ciba"`. `donkey.policy.type`
  reads `approval_denied`. It is terminal and never retried.

**Fix:** catch the new types ahead of `AuthError`. Don't refresh or replace
the credentials on `GatewayApprovalPending`, because they were accepted.
Re-send the same request after `retry_after` seconds.

```python
from donkey_kit import AuthError, GatewayApprovalDenied, GatewayApprovalPending

try:
    ...
except GatewayApprovalPending as exc:
    wait = exc.retry_after
except GatewayApprovalDenied:
    ...
except AuthError:
    ...
```

### 2. Google Model Armor and CrowdStrike AIDR blocks are `ContentSafetyBlocked`

**Who:** code that catches a Model Armor or CrowdStrike AIDR block as a plain
`PolicyViolation` and branches on `exc.policy == "unknown"`, or dashboards
that count it by `donkey.policy.type` (#1178, #1176).

**Symptom:** a `403` carrying `x-llm-proxy-model-armor-action: reject` or
`x-llm-proxy-crowdstrike-aidr-action: reject` was the generic
`PolicyViolation` with `policy == "unknown"`. It is now `ContentSafetyBlocked`,
with `policy == "content-safety"`, a `provider` (`google-model-armor` or
`crowdstrike-aidr`) and the flagged `categories`. `donkey.policy.type` reads
`content_safety` instead of `policy_violation`, and the span gains
`donkey.policy.provider` and `donkey.policy.categories` (#1183). The AIDR
shape is read from the policy's source, not captured live (ADR 0016).
`except PolicyViolation:` still catches both, and neither is retried.

**Fix:** catch `ContentSafetyBlocked` and read `exc.provider` in place of the
`policy == "unknown"` check. A `donkey.on(ContentSafetyBlocked)` handler now
fires for these blocks too.

### 3. `classify` is a `Classifier` object

**Who:** code that treats `donkey_kit.classify` or `donkey_kit.core.classify`
as a plain function (`inspect.isfunction()`, for example), or wraps it to add
mappings for its own gateway policies (#1152).

**Symptom:** it is now a `Classifier` instance. Calling it works as before,
with the same arguments, but `inspect.isfunction(classify)` is `False`. It
gains `register()`, `unregister()` and `registered()`. A registered rule
applies process-wide, after the built-in shapes and before the generic
upstream-error rule. `donkey_kit.core.errors.classify` is still a plain
function.

**Fix:** keep calling it. To type a custom policy's refusal, register the
shape on `classify` rather than wrapping it. The rule needs a `status` and at
least one header or body condition. It raises your own `PolicyViolation`
subclass, which must define its own `remediation`:

```python
from donkey_kit import PolicyViolation, classify


class OrderLimitExceeded(PolicyViolation):
    policy = "order-limit"
    remediation = "Split the order, or ask an admin to raise the order limit."


classify.register(
    match={"status": 403, "headers": {"x-order-limit": "exceeded"}},
    raises=OrderLimitExceeded,
)
```

### 4. `donkey publish` and `donkey verify` run, and no longer exit 3

**Who:** CI steps or scripts that run `donkey publish` or `donkey verify` and
rely on their 0.1.3 behaviour: hidden from `donkey --help`, and an immediate
exit 3 with `blocked on verification` (#218, #1352).

**Symptom:** both are now visible and do the work. `donkey publish` scans the
repository and publishes its MCP servers and agents to Exchange, at the
coordinates `[donkey.publication]` declares. `donkey verify` compares each
published MCP server and A2A agent with the live one, and writes nothing. Each exits 0 on
success, 1 on an issue or a failed asset, and 2 when there is nothing at the
path or the config lacks the control-plane credentials. A step that ran
either one as a no-op now publishes or fails. `donkey status` is still
hidden and still exits 3.

**Fix:** remove the step if it was a placeholder. To keep it, give it the
connected-app credentials and the scopes in
[`docs/scopes.md`](docs/scopes.md), and treat exit 1 as a failure. Run
`donkey publish --dry-run` first to see the plan without sending anything.

### 5. Registry, discovery and `ToolSet` calls reach Anypoint

**Who:** code that calls one of these and catches `NotImplementedError`
around it, or that never ran them because they always raised.

| Call | 0.1.3 | 0.2.0 |
| --- | --- | --- |
| `donkey.tools.discover()` | raised `NotImplementedError` | lists the MCP servers in Exchange (section 6) |
| `donkey.registry.search()` | raised | searches Exchange |
| `donkey.registry.resolve_mcp()` / `resolve_agent()` | raised | resolve an asset to its gateway endpoint |
| `donkey.registry.explain()` | raised | reads governed state from API Manager, Exchange and API Governance |
| `Publication.verify()` | raised | compares a published MCP server or A2A agent with the live one |
| The `ToolSet` binding methods | raised | bind the discovered servers to your framework |
| `derive_descriptor()`, `mode="auto"`, `"auto:live"`, `"auto:check"` | raised | read the schema the framework already computed |

**Symptom:** an `except NotImplementedError:` fallback around these calls is
no longer reached. They make control-plane calls with your connected app,
which needs the scopes in [`docs/scopes.md`](docs/scopes.md). A gateway whose
public URL is plain `http://` to a non-loopback host is refused, unless
`DONKEY_ALLOW_HTTP=1` is set. `derive_descriptor(mode="auto:static")`
now raises `ValueError`: that mode was dropped (#1252).

**Fix:** remove the fallback, or keep it only for the calls that still raise
`NotImplementedError` in 0.2.0. Those are `donkey.tools.lock()`,
`discover(locked=True)`, `donkey.registry.search(domain=)`,
`search(governed=)` or `search(environment=)`, `donkey.registry.warm()`,
`Publication.preview()`, `Publication.export()`, `publish_if_changed()`,
`Publication.verify()` for assets other than MCP servers and A2A agents, and
`donkey status`.

### 6. `donkey.tools.discover()` defaults to `governed=True`

**Who:** code that calls `discover()` without `governed=` and expects the
unfiltered list the 0.1.3 signature promised (ADR 0017, #1424).

**Symptom:** the set holds only the servers that pass the default
`GovernanceCriteria`, and each server left out is logged with its reasons.
When none pass, the set is empty, with a log line saying why, not an error.
`donkey.tools.warm()` uses the same default.

**Fix:** pass `governed=None` to list every server, with each verdict on
`McpServerHandle.governance`. The `ToolSet` binding methods still refuse a
set that holds an ungoverned server, with `UngovernedMcpServer`.

```diff
- tools = await donkey.tools.discover()
+ tools = await donkey.tools.discover(governed=None)   # every server, as 0.1.3 documented
```

### 7. Import `GovernanceCriteria` from `donkey_kit` again

**Who:** code that imports `GovernanceCriteria` from `donkey_kit.experimental`,
as 0.1.2 section 4 told you to (#1425).

**Symptom:** `DeprecationWarning: donkey_kit.experimental.GovernanceCriteria
is deprecated; import it from donkey_kit`. The name is back in
`donkey_kit.__all__`, and `donkey_kit.GovernanceCriteria` no longer warns.
`STRICT`, `AssetRef` and `RegistryError` stay in `donkey_kit.experimental`.

**Fix:**

```diff
- from donkey_kit.experimental import GovernanceCriteria
+ from donkey_kit import GovernanceCriteria
```

### 8. `[cli]` needs newer typer and rich, and no longer installs click

**Who:** an environment that pins `typer` below 0.26.0 or `rich` below 13.8.0
next to `donkey-kit[cli]`, or code that imports `click` and relied on
`donkey-kit[cli]` to install it (#1196).

**Symptom:** the `[cli]` floors move from `typer>=0.15.4` and `rich>=13.0` to
`typer>=0.26.0` and `rich>=13.8.0`, so `pip` reports a conflict with those
pins. Every typer up to 0.25.1 drops a command argument's help text under
click 8.5. typer 0.26.0 vendors click and no longer depends on it, so a fresh
install of `donkey-kit[cli]` has no `click`.

**Fix:** raise or drop the pins. If your own code imports `click`, depend on
it directly.

### 9. The `mcp` and `a2a` extras are back, and `[all]` and `[adk]` install more

**Who:** anyone who installs `donkey-kit[all]` or `donkey-kit[adk]`, or who
dropped `mcp` and `a2a` from an install line after 0.1.2 section 10.

**Symptom:** these extras change:

| Extra | 0.1.3 | 0.2.0 |
| --- | --- | --- |
| `mcp` | not provided | `mcp>=1.24`, for the `ToolSet` binding (#221) |
| `a2a` | not provided | `a2a-sdk[http-server]>=1.2.0` and `uvicorn>=0.30`, for `donkey.serve()` (#224) |
| `all` | `llm`, `langgraph`, `otel`, `cli`, `local` | adds `a2a` and `mcp` (#1436) |
| `adk` | `google-adk>=2.4`, `litellm>=1.84` | `litellm[mcp]>=1.84`: from litellm 1.106 a request with tools imports `mcp` (#1267) |

The base dependencies and `requires-python` are unchanged.

**Fix:** none needed unless your environment can't take the extra packages.
In that case install the extras you need instead of `[all]`. Add
`donkey-kit[mcp]` to bind discovered tools, and `donkey-kit[a2a]` to serve an
agent over A2A.

### 10. An `AgentKilled` stops that `Donkey` for good

**Who:** code that catches `AgentKilled` and keeps using the same `Donkey`, for
example to wait and retry once an administrator restores the agent (#220).

**Symptom:** a kill is terminal for the `Donkey` that saw it. From then on its
`donkey.budget.pace()` and `donkey.budget.wait_for_reset()` raise
`AgentKilled`, and a `wait_for_reset()` already asleep, on any event loop,
wakes and raises it. With telemetry on, DDK flushes its spans before the
`AgentKilled` reaches you, which can take up to 2 seconds per flush. The flush
is best effort.

**Fix:** let the process stop. If it must carry on after an administrator
restores the agent, build a new `Donkey`, which starts with a fresh budget.

### 11. `refusal_translator` leaves two adapters' `__all__`

**Who:** code that gets `refusal_translator` from
`from donkey_kit.integrations.agent_framework import *` or
`from donkey_kit.integrations.strands import *` (#1422).

**Symptom:** it is no longer in either module's `__all__`, so a star import
leaves it out and type checkers flag a direct import. A direct
`from donkey_kit.integrations.strands import refusal_translator` still works.
The typed-refusal bridge (`donkey.run()`, `@donkey.governed`,
`typed_refusals()`) calls it for you.

**Fix:** let the bridge do the translation. If you call it directly, import
it by name and expect it to change without notice.

### Budget headers (not breaking)

A rate-limit reset header that is negative, NaN, infinite or too large for a
`datetime` no longer makes `send()` raise `OverflowError`. It is ignored, and
the response's other budget headers still apply (#1444). A response that
arrives after a newer one no longer moves `donkey.budget` backwards (#311).
`pace()` is an admission check, not a reservation: tasks that enter it
together are admitted together, so size `reserve` to the width of your
fan-out.

### ADK `gemini()` and `model()` return subclasses (not breaking)

`donkey.adk.gemini()` and `donkey.adk.model()` return a subclass of ADK's
`Gemini` and `LiteLlm`, which carries `donkey.last_call` into an
`after_model_callback` on google-adk 2.13 and later (#1207).
`isinstance(llm, LiteLlm)` still holds, but `type(llm) is LiteLlm` does not.
`donkey_kit.integrations.adk.last_call_bridged()` gives the same bridge to a
model class of your own.

### OpenAI Agents SDK is a conformance-tested adapter (not breaking)

`donkey.openai_agents` moves to the conformance-tested tier next to LangGraph
(#223). Nothing about its factories changes. The OpenAI Agents SDK traces
every run by default, and whenever an OpenAI API key is available it sends
model inputs and outputs to OpenAI, outside the gateway and its policies. DDK
doesn't change that global setting for you (ADR 0010). Turn it off with
`agents.set_tracing_disabled(True)`, `RunConfig(tracing_disabled=True)` or
`OPENAI_AGENTS_DISABLE_TRACING=1` (#1434).

### New public names (not breaking)

Import these from `donkey_kit`:

- **Errors:** `GatewayApprovalPending`, `GatewayApprovalDenied` (#1320),
  `ApprovalRequired`, `ApprovalRejected`, `ApprovalTimedOut`,
  `ToolNotInProfile` and `UngovernedMcpServer`.
- **Approvals:** `Approval` and `Approvals` (`donkey.approvals`).
- **Tools and registry:** `ToolSet`, `McpAuth` and `GovernanceCriteria`.
- **Agents and A2A:** `AgentSpec`, `registered_agents`, `A2AResult` and
  `A2ATask` (also in `donkey_kit.a2a`).
- **Runs and identity:** `RunLedger`, `RunRefusal` (`donkey.current_run()`),
  `AttributionScope` (`donkey.attribution()`), `UserScope`
  (`donkey.as_user()`), `OnRefusal` (`donkey.on()`) and `Classifier`.
- **Budget:** `UpstreamQuota` and `UpstreamWindow` (`donkey.budget.upstream`,
  the upstream provider's own passthrough quota, read but never paced on)
  (#1025).

`donkey_kit.core` adds `PolicyRegistration`, `RefusalContext`, `RunUsage` and
`current_business_group`. The LangGraph, OpenAI Agents SDK, ADK and Strands
modules add approval helpers (`pending_approvals`, `approve`, `reject`), and
`donkey_kit.experimental` adds `tool_access_denial`.

`Donkey` gains `on()`, `attribution()`, `run(business_group=)`,
`run_from_headers()`, `correlation_headers()`, `as_user()`, `approvals`,
`serve()`, `agent()`, `current_run()`, `flush_telemetry()`,
`tools.warm()` and `tools.refresh()`, and `@donkey.tool` takes `approval=`.
`DonkeyConfig` gains `llm_proxy_key_header`, `user_token_header`,
`tool_warn_count` and `tool_warn_tokens`. The CLI gains `donkey --version`
and the `dev`, `scan`, `expose` and `preflight` commands, and `donkey mock`
gains a `ciba:` scenario. The conformance plugin gains `--donkey-mode` and the
`donkey_classify_registry` fixture.

### If you used a 0.2.0 pre-release

These changes landed between 0.2.0 pre-releases. They only affect code written
against one, because the surface was new in 0.2.0 or raised
`NotImplementedError` in 0.1.3.

- **`as_user(id_token=)` is `as_user(token=)`.** `token_exchange_path` and
  `DONKEY_TOKEN_EXCHANGE_PATH` are gone (#1420). `as_user()` also works as a
  plain `with` block in client-id mode (#1436).
- **`ToolSet.openai()` is `ToolSet.openai_agents()`,** with no alias. It
  returns the same list of servers (#1421).
- **Import `ToolNotInProfile` and `UngovernedMcpServer` from `donkey_kit`.**
  Their `donkey_kit.experimental` spellings emit a `DeprecationWarning`
  (#1425).
- **`discover()` and `tools.warm()` default to `governed=True`** (section 6).
  Pass `governed=None` for the old default (#1424).
- **`donkey dev --version X` is `--agent-version X`.** The old flag exits 2.
  The GitHub Action's `skipped` output is gone (#1428).
- **Pass the Action's `client-secret` from a `secrets.*` value.** A value
  from a variable or another step's output shows once in the log before the
  Action can mask it (#1442).
- **`--json` documents carry their own format key.** `publish --json` and
  `verify --json` carry `publish_format: 1` and `verify_format: 1` in place
  of `plan_format`. Only `publish --dry-run` keeps `plan_format`.
  `expose --json` and `preflight --json` carry `expose_format: 1` and
  `issues`. On a declaration issue each prints its own document, with every
  asset `not_attempted`. A script should check the format key before it
  reads a document (#1427).
- **`publish --json` prints its outcome document when the publish fails.**
  Each asset's `status` can be `incomplete`, `failed` or `not_attempted`, and
  the command exits 1. Rerun it to finish an `incomplete` publish.
  `donkey_kit.registry.exchange_publish.publish_plan()` returns a refusal on
  `outcome.error` instead of raising it (#1426).
- **Adapter approval helpers return `Approval` objects.** On Strands, ADK and
  the OpenAI Agents SDK, `pending_approvals()` returns `Approval` objects
  with the framework's own object on `.native`, and `approve()` and
  `reject()` return an `Approval`. Resume with the `.native` of each decided
  approval. Compare OpenAI Agents SDK approvals with `==`, not `is`.
  LangGraph gains the same helpers. `approved_call` and `approval_interrupt`
  are no longer in any adapter's `__all__` (#1422).
- **A resumed approval runs as the user who asked for it,** with that user's
  token, cost tags and business group. Resolving or resuming it inside
  `as_user()` with a different token raises `AuthError`, the tool doesn't
  run, and the approval stays pending. A call requested with no user bound
  is refused inside any `as_user()` block. Resume outside `as_user()`, or
  inside it with the requester's own token (#1416).
- **A per-tool MCP binding stays with its user.** A tool from `langgraph()`,
  `llamaindex()` or `anthropic()` raises `AuthError` when called under a
  different `as_user()` user from the one it was bound for. Bind the
  `ToolSet` inside the `as_user()` block that runs the agent, once per user
  or request (#1418).
- **A Strands approval covers exactly one call.** A nested call of the same
  tool from inside an approved call raises `ApprovalRequired` and waits for
  its own decision, as on ADK and the OpenAI Agents SDK (#1438).
- **`as_user()` refuses an `llm_proxy_url` from the working-directory config
  files** with a `ConfigError`. A `ToolSet` used inside the block refuses a
  `base_url` from those files that isn't a regional Anypoint host. Set the
  URL in the environment (`DONKEY_LLM_PROXY_URL`, `ANYPOINT_BASE_URL`) or in
  code, or set `DONKEY_TRUST_PROJECT_CONFIG=1` if you trust the checkout
  (#1439).
- **A gateway with a plain `http://` public URL is refused.** `resolve_mcp()`,
  `resolve_agent()`, `discover()` and `Publication.verify()` refuse a gateway
  whose public URL is `http://` to a non-loopback host, or carries a user name
  or password. `discover()` lists a server behind one with a failed
  `resolve_mcp` check and doesn't bind it. Give the gateway an `https://`
  public URL, or set `DONKEY_ALLOW_HTTP=1` on a network you control (#1417).
- **`ToolSet` bindings don't follow redirects** through their own HTTP
  client, or send the gateway credential to another origin. With Strands on
  `mcp` 2.1.1, an endpoint URL without its trailing slash now fails to
  connect. Use the trailing-slash URL, the only one the gateway routes
  (#1415).
- **`serve()` on a non-loopback host emits a `ConfigWarning`.** Filter it if
  you bind `0.0.0.0` on purpose behind a gateway. `donkey scan` fails on an
  endpoint whose query string carries a credential-named parameter, so move
  the credential out of the URL. A served A2A agent keeps only the 1000 most
  recent finished tasks for `GetTask` (#1443).
- **`ToolSet.wait_for_gateway_approval()` warns above two minutes.** It
  emits a `ConfigWarning` for a longer timeout, because the gateway
  re-prompts the user after about two minutes. When one MCP server's listing
  fails, the other servers' waits are cancelled instead of running to their
  timeout (#1437).
- **`tools.refresh()` also drops the registry's cached lookups,** and
  `tools.warm()` drops them before it reads, so both return current
  governance verdicts. A `donkey.registry.refresh()` call next to
  `donkey.tools.refresh()` is no longer needed (#1419).
- **CrowdStrike AIDR is `ContentSafetyBlocked` again,** after a pre-release
  that left it a generic `PolicyViolation` (#1429). A
  `classify.register()` rule that matches the AIDR header now raises
  `ValueError` at registration, because the built-in rule shadows it. Delete
  the rule and catch `ContentSafetyBlocked`.
- **Strands tool refusals propagate** through `ApprovalHook` as their typed
  error instead of becoming a tool result (#1237).

## 0.1.3: no breaking changes

Changes since `0.1.2` that you might notice. None of them breaks existing
code.

### Typed refusals through ADK `model()` (not breaking)

A gateway refusal on a `donkey.adk.model()` call inside `donkey.run()` or
`@donkey.governed` now leaves the block as the SDK's typed error, such as
`PIIDetected`, as 0.1.2 section 16 describes for the other adapters (#969).
Before, it left as LiteLLM's own error. Pass `typed_refusals=False` to get
LiteLLM's errors as before. A `LiteLlm` built from `connection_kwargs()`, or a
`model()` given your own `llm_client`, still raises LiteLLM's error.

### CA and JP control-plane hosts (not breaking)

The `ca` and `jp` regions now point at `ca1.platform.mulesoft.com` and
`jp1.platform.mulesoft.com`. Before, they pointed at `ca1.anypoint.` and
`jp1.anypoint.`, which don't exist, so every control-plane call in those
regions failed. Choosing a region no longer emits an `UnverifiedValueWarning`
(#22).

## 0.1.2: public API surface, removed control plane and typed errors

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

**Fix:** remove the call. `donkey status` is still there, hidden, and still
exits 3 until its platform reads (BG §2.5) are verified. `donkey publish` (#218)
and `donkey verify` (#1352) have since been implemented.

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
config file; a `base_url` on `anypoint.mulesoft.com`, `eu1.anypoint.mulesoft.com`,
`ca1.platform.mulesoft.com` or `jp1.platform.mulesoft.com` (before 0.1.3 the
CA and JP entries were `ca1.anypoint.` and `jp1.anypoint.`, which do not exist, #22).

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

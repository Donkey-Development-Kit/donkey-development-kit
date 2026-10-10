# Python API reference

The public API of `donkey-kit`, generated from its
signatures and docstrings. Each entry shows the first paragraph of the
docstring; `help()` on the object prints the rest. Everything listed here is
importable from `donkey_kit`.

## `Donkey`

The SDK entry point: one governed handle on Agent Fabric.

Each framework adapter is an attribute too, listed under
[Framework adapters](#framework-adapters).

```python
Donkey(
    config: DonkeyConfig | None = None,
    *,
    auth: AuthProvider | None = None,
    llm_auth: AuthProvider | None = None,
) -> None
```

### `Donkey.from_env()`

```python
Donkey.from_env(
    *,
    team: str | None = None,
    project: str | None = None,
    env: str | None = None,
    enduser_id: str | None = None,
    path: str | os.PathLike[str] | None = None,
    **overrides: Unpack[ConfigOverrides],
) -> Donkey
```

Build from the environment and the config files, with any `DonkeyConfig` field set in code on top.

Guide: [`/reference/configuration#precedence`](https://docs.donkey-kit.dev/reference/configuration.md#precedence)

### `donkey.config`

```python
donkey.config: DonkeyConfig
```

The resolved configuration this Donkey was built with (read-only).

Guide: [`/reference/configuration`](https://docs.donkey-kit.dev/reference/configuration.md)

### `donkey.llm`

```python
donkey.llm: LLMClient
```

The governed model-call surface: native OpenAI-compatible clients and per-framework connection kwargs pointed at the proxy.

Guide: [`/quickstart`](https://docs.donkey-kit.dev/quickstart.md)

### `donkey.budget`

```python
donkey.budget: Budget
```

The token-budget window for this Donkey, updated in-band from every response's `x-token-*` headers. Unobserved (all fields `None`) until the first call returns; there is no budget-query endpoint, so it is only as fresh as `budget.observed_at`.

### `donkey.last_call`

```python
donkey.last_call: LastCall
```

The gateway's own metadata about the most recent governed model call in this context — its `request_id`, `api_instance_id` and `environment_id`; what the gateway *did* with the request — `served_provider` / `served_model` / `routing_type` and the `fallback` flag, with `substituted` true when the served model differs from `requested_model`; and the per-call usage token counts (`input_tokens` / `output_tokens` / `total_tokens` and the cost-relevant `cached_tokens` / `cache_write_tokens` / `reasoning_tokens`). The success-path counterpart to the ids `DonkeyError` hands you on a refusal.

### `donkey.openai()`

```python
donkey.openai(*, sync: bool = False, **kw: Any) -> AsyncOpenAI | OpenAI
```

The headline two-line ergonomic: a native OpenAI client pointed at the governed proxy, with nothing new to learn.

### `donkey.http_client()`

```python
donkey.http_client(*, sync: bool = False) -> DonkeyAsyncClientView | DonkeyClientView
```

The governed data-plane `httpx` client, for a framework or SDK that takes one (`http_client=`, `async_http_client=`, a pre-built `AsyncOpenAI`). Requests sent through it get the same headers, retries, span, budget and `donkey.last_call` as every adapter.

### `donkey.registry`

```python
donkey.registry: ExchangeRegistry
```

The Exchange registry client used for tool discovery and resolution.

Guide: [`/tool-access/discovery`](https://docs.donkey-kit.dev/tool-access/discovery.md)

### `donkey.tools`

```python
donkey.tools: ToolsFacade
```

Governed tool discovery and the `donkey.lock` lockfile.

Guide: [`/tool-access`](https://docs.donkey-kit.dev/tool-access.md)

### `donkey.run()`

```python
donkey.run(
    id: str | None = None,
    *,
    team: str | None = None,
    project: str | None = None,
    env: str | None = None,
    enduser_id: str | None = None,
    typed_refusals: bool = True,
) -> RunScope
```

Group one logical agent run under a shared correlation id.

### `donkey.run_context()`

```python
donkey.run_context(run_id: str | None = None) -> RunScope
```

Deprecated alias for `run`: use `donkey.run(id=…)`. Emits a `DeprecationWarning`.

### `donkey.cache()`

```python
donkey.cache(
    *,
    skip: bool | None = None,
    no_store: bool | None = None,
    ttl: int | None = None,
    threshold: float | None = None,
    principal_id: str | None = None,
) -> CacheScope
```

Steer the gateway's **semantic cache** for every governed model call in the block.

### `donkey.governed()`

```python
donkey.governed(
    func: Callable[P, R] | None = None,
    *,
    team: str | None = None,
    project: str | None = None,
    env: str | None = None,
    enduser_id: str | None = None,
    typed_refusals: bool = True,
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]
```

Wrap a callable so its body runs inside a `donkey.run()` scope.

### `donkey.tool()`

```python
donkey.tool(func: Callable) -> Callable
```

Mark a callable as a governed tool **without changing call behaviour**, recording its name, signature and docstring in an introspectable registry.

### `donkey.simulate()`

```python
donkey.simulate(
    error: type[DonkeyError],
    *,
    times: int = 1,
) -> AbstractContextManager[None]
```

Inject a real gateway refusal in-process, no server.

### `donkey.aclose()`

```python
async donkey.aclose() -> None
```

Close every transport this Donkey owns: the data-plane and control-plane clients, the connected-app token-fetch client it built (a caller-supplied `auth` provider stays caller-owned), and the blocking client. Each one is closed even if an earlier close raises.

### `donkey.close()`

```python
donkey.close() -> None
```

Close the blocking transport.

## `DonkeyConfig`

Everything the SDK needs to reach Agent Fabric, resolved once and immutable.

Its fields are listed in [Configuration](https://docs.donkey-kit.dev/reference/configuration.md#all-settings).

### `DonkeyConfig.resolve()`

```python
DonkeyConfig.resolve(
    *,
    path: str | os.PathLike[str] | None = None,
    **overrides: Unpack[ConfigOverrides],
) -> DonkeyConfig
```

Resolve every field along the configured precedence.

Guide: [`/reference/configuration#precedence`](https://docs.donkey-kit.dev/reference/configuration.md#precedence)

### `DonkeyConfig.from_env()`

```python
DonkeyConfig.from_env() -> DonkeyConfig
```

Build from env + the optional config files: `resolve` with no arguments (see the module docstring). Does not validate; call `validated` when you know which capability you need.

### `config.control_plane_url`

```python
config.control_plane_url: str
```

The Anypoint control-plane base URL — explicit override or region.

### `config.source_of()`

```python
config.source_of(name: str) -> ConfigSource
```

Where field `name` was resolved from. `explicit` (set in code) when `resolve` did not resolve it or its value has changed since, e.g. through `dataclasses.replace`, `with_overrides` or direct construction; copies that keep the value keep the label, and so does `DonkeyConfig(**dataclasses.asdict(cfg))` in the same process. A label recorded in another process can't be checked against the value: an endpoint keeps it and a credential counts as set in code.

### `config.with_overrides()`

```python
config.with_overrides(**kw: Unpack[ConfigOverrides]) -> DonkeyConfig
```

Return a copy with the given fields replaced.

### `config.validated()`

```python
config.validated(*, need: Capability = 'control_plane') -> DonkeyConfig
```

Return self if valid for the requested capability, else raise a `ConfigError` listing EVERY missing field at once.

### `config.missing_fields()`

```python
config.missing_fields(*, need: Capability) -> list[str]
```

Every required field for `need` that is unset, each with the env var that sets it. The list `validated` reports.

### `config.check_endpoints()`

```python
config.check_endpoints(*, need: Capability, code_credential: str | None = None) -> None
```

Raise `ConfigError` if `need`'s endpoint may not receive the credentials that would be sent to it.

## `LLMClient` (`donkey.llm`)

The framework-free proxy client factory.

### `donkey.llm.close()`

```python
donkey.llm.close() -> None
```

Close the blocking client built by this standalone factory, if any.

### `donkey.llm.aclose()`

```python
async donkey.llm.aclose() -> None
```

Close this factory's owned blocking client in an async scope.

### `donkey.llm.client()`

```python
donkey.llm.client(*, sync: bool = False, **kw: Any) -> AsyncOpenAI | OpenAI
```

An OpenAI client pointed at the LLM proxy, using our shared http client so headers + retries apply.

### `donkey.llm.list_models()`

```python
async donkey.llm.list_models(*, live: bool = False) -> list[ModelHandle]
```

List logical models the proxy exposes.

### `donkey.llm.resolve()`

```python
donkey.llm.resolve(model_id: str, *, provider: str | None = None) -> ModelHandle
```

A heuristic `ModelHandle` for a known model id.

## `Budget` (`donkey.budget`)

The token-budget window for one `Donkey`, updated in-band from each response's budget headers (numeric `x-token-*` or the prose `x-llm-proxy-ratelimit` fallback). Its request-count sibling is `requests`.

### `donkey.budget.observe()`

```python
donkey.budget.observe(response: httpx.Response, *, now: datetime | None = None) -> None
```

Update from a response's budget headers.

### `donkey.budget.pace()`

```python
donkey.budget.pace(
    *,
    reserve: float = 0.0,
    now: datetime | None = None,
) -> AbstractAsyncContextManager[None]
```

Guard a request so it is refused *before* it crosses your reserve, not after a 429 comes back.

### `donkey.budget.wait_for_reset()`

```python
async donkey.budget.wait_for_reset(
    *,
    window: str | None = None,
    now: datetime | None = None,
) -> None
```

Sleep until the tripped window's `reset_at`, then return — the recovery half of pacing.

## `LastCall` (`donkey.last_call`)

What the gateway said about the most recent governed model call.

Its fields are listed in [`last_call` fields](https://docs.donkey-kit.dev/reference/last-call.md).

### `donkey.last_call.observed`

```python
donkey.last_call.observed: bool
```

True iff a governed response actually populated this record.

### `donkey.last_call.substituted`

```python
donkey.last_call.substituted: bool
```

True iff the gateway served a *different* model than the one requested — a silent model substitution the developer's cost model, token assumptions and evaluation are otherwise blind to. Requires both the requested and served model to be known; a missing either side is not a substitution claim. A `provider/` prefix naming `served_provider` is not a difference — see `is_substitution`.

### `donkey.last_call.cache_hit`

```python
donkey.last_call.cache_hit: bool
```

True iff the gateway served this call from its semantic cache (`cache_status` == `"hit"`) — a verbatim replay with no provider round-trip. The zero-spend read a cost rollup uses to exclude a hit's replayed `usage` from fresh spend. `False` for every other status and for an unobserved / non-cached response.

### `donkey.last_call.available`

```python
donkey.last_call.available: bool
```

False only when the current surface structurally cannot be observed (the framework owns the transport, or the adapter gets only `default_headers`); a plain cold read is still `available` — it just has not observed anything yet.

### `LastCall.from_response()`

```python
LastCall.from_response(
    response: httpx.Response,
    *,
    requested_model: str | None = None,
    now: datetime | None = None,
) -> LastCall
```

Build an `OBSERVED` record from a governed response's headers.

## Framework adapters

`donkey.<attribute>` imports the adapter on first use. If its framework is
not installed, the access raises `ImportError` with the `pip install` line.
See [Frameworks](https://docs.donkey-kit.dev/frameworks.md) for what each factory returns.

| Attribute | Install | Factories |
|---|---|---|
| `donkey.langgraph` | `pip install "donkey-kit[langgraph]"` | `chat_model()` |
| `donkey.adk` | `pip install "donkey-kit[adk]"` | `model()`, `gemini()` |
| `donkey.strands` | `pip install "donkey-kit[strands]"` | `model()` |
| `donkey.agent_framework` | `pip install "donkey-kit[agent_framework]"` | `chat_client()` |
| `donkey.openai_agents` | `pip install "donkey-kit[openai-agents]"` | `model()` |
| `donkey.anthropic` | `pip install "donkey-kit[anthropic]"` | `client()` |
| `donkey.crewai` | `pip install "donkey-kit[crewai]"` | `llm()` |
| `donkey.llamaindex` | `pip install "donkey-kit[llamaindex]"` | `llm()` |

## Exceptions

Every typed refusal and SDK error. See [Typed refusals](https://docs.donkey-kit.dev/errors.md) for when each
is raised.

| Exception | Base | Meaning |
|---|---|---|
| `AgentKilled` | `PolicyViolation` | The gateway's Agent Kill Switch blocked this agent — it is quarantined in Governance > Security, or listed in the policy's *Killed Agent IDs*. |
| `AuthError` | `DonkeyError` | Rejected credentials on the Anypoint control plane or LLM-proxy data plane. |
| `BudgetReserveReached` | `DonkeyError` | Raised by `Budget.pace` *before* it lets a request through that would cross the caller's reserve. |
| `ConfigError` | `DonkeyError` | Configuration is missing or invalid, or an endpoint may not receive the credentials that would be sent to it (see `DonkeyConfig.check_endpoints`). Raised locally before any request; reports ALL missing fields at once. The transport also raises it, with its own remediation, for a send on a closed client or from a closed event loop. |
| `ContentSafetyBlocked` | `PolicyViolation` | A provider-backed content-moderation policy (Azure Content Safety or Amazon Bedrock Guardrails) blocked the request or response. |
| `DonkeyError` | `Exception` | Base for all SDK errors. Carries correlation/call/request IDs and the raw response so callers can inspect what actually happened. |
| `GatewayUnavailable` | `DonkeyError` | The gateway could not be reached at all — a transport-level failure (DNS, refused connection, TLS error, timeout) with NO HTTP response behind it. |
| `ModelNotRoutable` | `DonkeyError` | The gateway could not pick a provider for the requested model, so it rejected the request before any upstream call. |
| `ModelSubstituted` | `DonkeyError` | The gateway served a *different* model than the one requested — a routing fallback substituted the model, and `on_model_substitution="raise"` opted the caller into treating that as an error. |
| `PIIDetected` | `PolicyViolation` | The PII-detection policy blocked the request or response. |
| `PolicyViolation` | `DonkeyError` | Base for gateway-enforced refusals. NEVER retried. |
| `PromptInjectionBlocked` | `PolicyViolation` | The prompt-injection-protection policy flagged the request as an injection attempt. |
| `RequestRateLimitExceeded` | `PolicyViolation` | A request-rate-limit policy refused the request: the window's request count is spent. |
| `TokenBudgetExceeded` | `PolicyViolation` | A token-rate-limit policy refused the request: the budget window is spent. |
| `UpstreamModelError` | `DonkeyError` | Provider-side failure (5xx). Retryable. |
| `UpstreamRequestError` | `DonkeyError` | The upstream provider rejected the request (4xx), passed through the gateway verbatim (e.g. OpenAI `model_not_found`). This is a client-side mistake, NOT a gateway policy refusal and NOT a provider outage, so it is terminal (never retried) but distinct from `PolicyViolation`. |

## Other exports

| Name | Kind | Meaning |
|---|---|---|
| `Budget` | class | The token-budget window for one `Donkey`, updated in-band from each response's budget headers (numeric `x-token-*` or the prose `x-llm-proxy-ratelimit` fallback). Its request-count sibling is `requests`. |
| `CacheControls` | class | The per-request semantic-cache steering controls. |
| `CacheScope` | class | A **dual sync/async** context manager that applies `CacheControls` to the governed calls made in its block. |
| `ConfigOverrides` | class | The public `DonkeyConfig` fields, each optional, as keyword arguments: what `DonkeyConfig.resolve`, `DonkeyConfig.with_overrides` and `Donkey.from_env` accept, so a misspelt field fails type checking. |
| `CostTags` | class | The fixed four-dimension cost-attribution set. |
| `Donkey` | class | The SDK entry point: one governed handle on Agent Fabric. |
| `DonkeyAsyncClientView` | class | A non-owning `httpx.AsyncClient` over a shared `DonkeyAsyncClient`. |
| `DonkeyClientView` | class | The blocking twin of `DonkeyAsyncClientView`, over a shared `DonkeyClient`. Get one from `DonkeyClient.view`. |
| `DonkeyConfig` | class | Everything the SDK needs to reach Agent Fabric, resolved once and immutable. |
| `ExchangeRegistry` | class | The Exchange client behind `donkey.registry`: search, resolve and explain assets. |
| `LLMClient` | class | The framework-free proxy client factory. |
| `LastCall` | class | What the gateway said about the most recent governed model call. |
| `LastCallStatus` | class | Why `LastCall` fields are (or are not) populated — so a `None` is never ambiguous between "the gateway said nothing" and "the SDK never saw the response". `str`-valued so it prints and logs cleanly. |
| `Region` | type alias | One of `us`, `eu`, `ca`, `jp`. |
| `RequestWindow` | class | The gateway's request-count window, `donkey.budget.requests`. |
| `RunScope` | class | A **dual sync/async** context manager that sets the run correlation id for the governed calls made in its block. |
| `ToolSpec` | class | An introspectable record of a `@donkey.tool`-marked callable. |
| `ToolsFacade` | class | `donkey.tools` — discovery + lock. |
| `TypedRefusals` | class | Re-raise a refusal from the block as its typed `DonkeyError`. |
| `classify` | function | Map an HTTP error response to a specific exception. |
| `registered_tools` | function | Every callable marked with `@donkey.tool` in this process, in decoration order. The introspection entry point for the scanner / card generator. |
| `typed_refusals` | function | Re-raise a governance refusal from the block as its typed `DonkeyError`. |

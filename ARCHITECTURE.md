# Architecture

This document is the contributor-facing map of how the SDK is built — the layer
boundaries, the design invariants, and the discipline that keeps the package
trustworthy. It is a distillation, not the spec. The authoritative specs are
[`spec/donkey-development-kit-build-plan.md`](spec/donkey-development-kit-build-plan.md)
(phases, milestones, standing invariants) and
[`spec/donkey-development-kit-build-guide.md`](spec/donkey-development-kit-build-guide.md)
(feature scope, cited as `BG §N.N`). The five **standing invariants** in the
build plan keep a bare `§` label — `§0.3`, `§1.1`, `§2.1`, `§8.1`, `§8.4` — as
their stable historical name; every other citation is a `BG §N.N`, a named
invariant, or a `Phase N`. When a rule here feels arbitrary, read the cited
section — the constraints are deliberate.

For *using* the SDK, see the consumer docs site (`website/`). For *working in*
the repo — branch/PR flow, testing surfaces, coding conventions — see
[`CONTRIBUTING.md`](CONTRIBUTING.md).

![Donkey Development Kit — from your framework, through the SDK's config/native-object/transport/classify stages, to the governed Omni Gateway and upstream model providers.](website/public/img/sdk-architecture.png)

The SDK is a thin, framework-native client for a **governed gateway** — but it is
*not* sold as a way to reach the Omni Gateway, because a stock OpenAI client with a
`base_url` and two headers already does that. Its value is structural: **the
wrapper is the skeleton** (`BG §1.1`). It is the single point in the process where
every request enters and every response leaves, so it is the only place where
budget headers, error classification, correlation IDs, cost tags, OTel spans,
simulation, and refusal handlers can all attach without the developer wiring each
one.

The skeleton is worth exactly the sum of what hangs on it — which is why the
**six-piece minimum** ships as one milestone rather than spread across the
roadmap: typed refusals, a budget object + pacing, a local gateway simulator,
`simulate()` + the conformance plugin, OTel GenAI instrumentation, and
correlation IDs + cost tags (`BG §1.2`–`BG §1.7`). Your agent code stays in
whatever framework you already use; the SDK builds that framework's *own* native
client object and attaches governance and attribution to the one transport it
owns. The gateway — not the SDK — enforces policy.

---

## Layered architecture

The package is a strict stack. Higher layers depend on lower layers; **no lower
layer may import a higher one.**

```
integrations/   per-framework adapters — return NATIVE framework objects, never wrappers
      ↓         (langgraph · adk · strands · agent_framework · openai_agents · anthropic · crewai · llamaindex)
tools/          MCP session management, tool discovery/filtering — resolves registry handles
      ↓
registry/       Exchange discovery, typed governed-state assets
      ↓
llm/            framework-free OpenAI-compatible client factory + model catalog
      ↓
core/           config · auth · transport · errors · budget · telemetry · cache · _verify  — ZERO framework deps (httpx only; the build plan also allows pydantic, but core imports none — value objects are frozen dataclasses, ADR 0001)
```

**Not in the stack — `provisioning/`.** The package holds two different things:

- **The refused declarative control plane** (specs · plan/diff/apply · drift ·
  governance lint · publish). It is on the build plan's *Do not build* list —
  it must not compete with API Manager/Terraform — and its CLI commands
  (`validate`, `plan`, `apply`, `drift`, `lint`, `generate`, `status`,
  `publish`, `verify`) are hidden and `_verify.blocked`. Treat this half as
  legacy scaffolding: reachable, but do not deepen it (see "Still blocked",
  below).
- **The supported `donkey` CLI.** The console script points at
  `donkey_kit.provisioning.cli:app`, and its visible commands — `init`, `test`,
  `mock`, `doctor` — are live. Moving them to their own `donkey_kit/cli`
  package and quarantining the legacy half is #730.

**The hard rule (the layered architecture):** `core/` has no dependency on any agent framework.
Each `integrations/*` adapter may depend on exactly one framework, and nothing
in `integrations/` may be imported by `core`, `llm`, `registry`, or `tools`.
This is enforced in CI by `import-linter` (`lint-imports`); a violating import
fails the build. A companion `independence` contract additionally forbids one
adapter from importing another (directly or indirectly), so a convenient
cross-import can never drag a second framework's optional dependency onto an
install that asked only for the first — the shared `_base` sibling is exempt,
since every adapter importing it downward is expected. The `base-only` CI job
additionally installs *only* the base package and imports `donkey_kit` to catch
an accidental top-level framework import leaking into a lower layer.

Because of that rule, adapters import their framework **lazily, inside methods** —
never at module top level — so importing the base package never drags in a
framework that may not be installed.

**Value objects are frozen dataclasses** ([ADR 0001](docs/adr/0001-value-objects.md)).
Config, catalog entries, registry assets and results are `@dataclass(frozen=True)`
and change by building a new instance (`dataclasses.replace(...)`,
`DonkeyConfig.with_overrides(...)`). pydantic is used only at an external-schema
boundary. The one such module today is the legacy `provisioning/spec.py`, which
validates a user-authored YAML spec, and it is the only reason `pydantic` is
still a base dependency. Dropping it from the base install is part of #730.

### How the pieces connect

- **`Donkey`** is the public surface and orchestrator. It owns one shared
  **`DonkeyAsyncClient`** (an `httpx.AsyncClient` subclass that injects the
  governance/attribution headers) **per credential plane**, so each credential
  only ever rides its own plane's requests (`BG §1.1`):
  - the **data-plane** client goes to the LLM client and every adapter, so there
    is exactly one transport and one header-injection point for model calls. It
    carries only the LLM-proxy credential: the `client_id`/`client_secret` header
    pair in the default client-id mode (no token provider), or the model-wallet
    JWT from `Donkey(llm_auth=…)` in `jwt` mode. Frameworks never get this
    client itself: they get its non-owning view (`DonkeyAsyncClient.view()`,
    public as `Donkey.http_client()`), which sends through it and whose close
    is a no-op, so a framework that closes its client (Strands, `async with`)
    can't end the pool. Only `Donkey.aclose()`/`close()` do (#733);
  - the **control-plane** client backs the registry and any other Anypoint
    platform call, authenticated by the connected-app token (`Donkey(auth=…)`,
    or the default `AnypointConnectedApp`). It never carries the wallet JWT or
    the wallet selector, and data-plane calls never fetch or carry its token.
    Both clients inject the same correlation, attribution, cache-control and
    (opt-in) cost-tag headers; only the credentials differ.

  A framework built on `httpx2` that rejects `httpx` clients (`anthropic>=1.0`,
  #701) gets an `httpx2.AsyncClient` from `integrations/_httpx2_bridge.py`
  instead, whose transport forwards every request through the same data-plane
  client, so it is still one transport.

  `Donkey` builds these through a **`Runtime`** (`core/runtime.py`), which owns
  the config, the OTLP bootstrap, the auth providers, the `Budget`, both clients
  and their close; it is the only place shared clients are built. The
  module-level factories (`donkey_kit.integrations.<framework>.<factory>()`) run
  on the process-default runtime from `runtime.default()`: built lazily under a
  lock from the environment, exactly as `Donkey.from_env()` would be, shared by
  every factory, and closed at interpreter exit. It lives in `core` because
  `integrations` may not import the top package (#725).

  Each client attaches credentials only to its **checked endpoints**
  (`_CheckedEndpoints` in `core/transport.py`), compared by scheme, host and
  port: the plane's configured URL, plus any URL override a factory accepted
  after the same https check (`allow_endpoint`). A request to any other origin,
  every redirect hop included (httpx runs request hooks per hop), goes out with
  the credential headers removed. The clients don't follow redirects. The
  blocking `DonkeyClient` shares the async client's origin set. CrewAI, whose
  provider builds its own client, gets an `interceptor` that applies the same
  rule.
- **Adapters are lazy attributes.** `Donkey.__getattr__` resolves
  `donkey.<framework>` on first access through the `ADAPTERS` registry declared
  in `integrations/__init__.py`. Accessing an adapter whose optional extra is not
  installed raises an `ImportError` carrying the exact `pip install` command —
  never a bare `ModuleNotFoundError`. The registry probes every module an
  adapter needs, not just the framework's own, and each factory wraps its lazy
  import in `Adapter._native_import()`, so the adapter method and the
  module-level factory raise the same error. Each adapter returns the framework's own
  object (e.g. a real `langchain_openai.ChatOpenAI`), so there is nothing to
  unlearn and a three-line escape hatch (`connection_kwargs()`) out of the SDK.
- **Configuration** resolves per key (§2.1, `DonkeyConfig.resolve`): values
  set in code (`resolve()` / `Donkey.from_env()` keyword arguments,
  `with_overrides`) → env vars → `./.donkey-kit.local.toml` →
  `./.donkey-kit.toml` → `$XDG_CONFIG_HOME/.donkey-kit.toml` (or
  `~/.config/.donkey-kit.toml` when that variable is unset or empty) → default.
  The three files merge key by key, nested tables recursively, so a lower file
  fills what a higher one leaves unset; `resolve(path=...)` reads a named file
  in place of `./.donkey-kit.toml`. One declarative field table (`_FIELDS` in
  `core/config.py`) names each field's env var, TOML key, parser and value
  check; the loader, the error messages and the configuration docs (pinned by a
  unit test) all follow it. `DonkeyConfig(...)` built directly reads neither env
  nor files. It reports every missing field, and every invalid value, at once
  rather than one failure per run. `Donkey.from_env()` is the entry point.
  Each field records its source
  (`DonkeyConfig.source_of`); a value that differs from the loaded one counts as
  set in code, however it was changed. So `llm_proxy_url` or `base_url` read
  from the working directory's files only receives credentials from those files
  (`DonkeyConfig.check_endpoints`), loopback included. Those two URLs and the
  token endpoint must be `https://` except loopback, unless `DONKEY_ALLOW_HTTP`
  is set in the environment. The check runs before a credential leaves on
  either plane: in `validated()` for model calls and the registry, before each
  control-plane token fetch, and before a `Donkey(auth=…)` provider's token is
  requested. Governance `[targets.*].base_url` is not covered yet (#832).
  Provenance stores a keyed digest of each loaded value (the key is random per
  process), so `asdict()` copies no secret and a config rebuilt in another
  process keeps its URL sources but treats its credentials as set in code. A
  working-directory config file that resolves outside the directory is
  refused. `DonkeyConfig.__post_init__` checks the configurable header names
  against `core/header_names.py`: a name must start with `X-` and must not be a
  routing, framing, method/path-override, credential, SDK-set or framework-set
  header, or collide with another configured name.
- **Printed output.** `core/masking.py` holds the one list of credential key
  names (`SENSITIVE_NAMES`). Every `connection_kwargs()` and
  `proxy_auth_headers()` returns a `MaskedDict` that prints `'***'` for them;
  `DonkeyConfig` leaves its secret fields out of `repr`; `PIIDetected` rebuilds
  its message without the flagged values; a typed error mapped from a framework
  error is raised `from None`, with the original on `framework_error`. Framework
  objects built from the kwargs keep their own `repr`, and some of them print
  credentials.
- **The transport is the attachment point.** `DonkeyAsyncClient` exposes four
  internal lifecycle hooks — no-op by default, **not** public API, mirrored on the
  sync twin `DonkeyClient` — so the six-piece minimum *attaches* rather than
  re-wiring `send()` (`BG §1.1`, #179/#287). This is what makes the skeleton one
  milestone instead of six ad-hoc integrations:

  | Hook | When it fires | What attaches |
  | --- | --- | --- |
  | `_on_request` | once, before the retry loop | no-op seam today; see the note below the table |
  | `_on_response` | once, on the final response (via `_finish()`) | `Budget` parse from `x-token-*` (`BG §1.3`); the `donkey.last_call` record (#362) |
  | `_on_refusal` | never — no caller today; `classify()` raises the typed error directly, and the LangGraph bridge maps it without the hook | typed-refusal reaction handlers (`BG §1.2`, #208); the framework-agnostic typed-refusal bridge is #724 |
  | `_swap_transport` | fixture seam | `simulate()` (#190) and the conformance harness (#191) swap a fixture in, only through the private `donkey_kit._testing` seam module (#719) (`BG §1.4`/`BG §1.5`); the constructors fold httpx's proxy mounts into the base transport, so a swap covers every route and fails closed if a mount appears later (#801) |

  Correlation, attribution, cache-control and opt-in cost-tag headers
  (`BG §1.7`) are set on every attempt by the `_inject_headers` request event
  hook, not by `_on_request`. The OTel span (`BG §1.6`) opens in `send()` and
  closes there, or in the stream wrapper for a streamed response. Its response
  attributes, including the `classify()`-derived policy decision, are recorded
  by `_record_response`, which `_finish()` calls right after `_on_response`.

  Three contracts matter: **override the hook, not `send()`**; a subclass that
  overrides `_on_response` **must call `super()._on_response(...)`** or budget
  tracking silently breaks; and because a transport-level error escapes before
  `_finish` runs, anything opened in `_on_request` has **no paired
  `_on_response`** on that path — such a consumer must close in a `finally`,
  never relying on the response hook (the OTel span avoids this by living in
  `send()`).
  A hookless client behaves exactly as it did before the hooks were added. The
  full contracts live in the `core/transport.py` docstrings.
- **`Governance`** (`governance.py`) is legacy scaffolding outside the linear
  import stack — it depends only on `core` and remains reachable from its module,
  but it is not exported as first-class `donkey_kit` API. It is ONE object behind
  three verbs: `simulate()` (an ephemeral local
  gateway harness), `export()` (emit the governed-state manifest), and `resolve()`
  (reconcile a running `Donkey` against it, raising `GovernanceDrift` on
  mismatch); a separate platform-team-only `apply()` is the deliberate escape
  hatch. **All of these are currently `_verify.blocked`** — the `simulate()`
  harness included — pending the Verification milestone, so today the object is the
  shape, not yet the behaviour. The governed-state *asset* criteria one layer
  down (`GovernanceCriteria`, `STRICT`, `evaluate`) live in `registry/criteria.py`
  (renamed from `registry/governance.py` in #719 so the two modules no longer
  share a name; the old path is a deprecated alias).

Every governed surface ships in three ergonomic forms that must stay in lockstep:
the `donkey.<framework>` factory, a `connection_kwargs()` accessor, and a
module-level factory. Nothing checks this structurally yet: the lockstep is held
by hand-written tests in `tests/unit/test_adapter_ergonomics.py`
(`test_factory_and_connection_kwargs_do_not_drift` and its per-adapter
siblings), whose `_FACTORIES` table a new adapter must be added to by hand.
Formalising the adapter contract and its roster is #726.

---

## Verification discipline

This is the SDK's most distinctive principle and its strongest trust guarantee.

> **Never invent an endpoint, header name, or class name.**

A fabricated endpoint that 404s in a customer sandbox destroys confidence in the
whole package, so the codebase makes fabrication structurally hard. `core/_verify.py`
is the single home for every value that the verification discipline says must be confirmed against a real
Anypoint sandbox before it can be trusted, and it offers exactly two mechanisms:

- **`blocked("…")`** returns a `NotImplementedError("blocked on verification: …")`.
  It is used where there is no defensible placeholder at all — e.g. the MCP-bridge
  tool-discovery and the provisioning control-plane endpoints. The SDK raises
  rather than guesses. **Do not replace a `blocked(...)` guard with a guess.**
- **`Unverified(...)`** placeholder constants hold a documented best-guess that is
  fully overridable via config/env, and emit a one-time `UnverifiedValueWarning`
  the first time they are read — so a value can be *used* without ever being
  *mistaken for confirmed*. A customer can point it at the real value immediately;
  we don't block them waiting on our own verification.

**How a value flips to verified.** When a value is confirmed against a sandbox,
two edits move together, never apart: flip its row in
[`docs/verified-apis.md`](docs/verified-apis.md) to `VERIFIED`, **and** replace
its `Unverified(...)` entry in `core/_verify.py` with a plain constant so the
warning stops firing. `docs/verified-apis.md` is the single source of truth for
what is verified and the worklist of what is still blocked. Code outside
`core/_verify.py` cites it (`docs/verified-apis.md §N`) and never restates a
status or a date; `scripts/check_verification_claims.py` enforces that in CI.

What is verified today: the LLM-proxy data plane (its base-URL shape — note there
is **no `/v1`** — the `client_id`/`client_secret` request-header pair, streaming,
and the live rejection shapes), the OAuth2 control-plane token path, and the
CLI-plugin REST contract (from static analysis). Still blocked: Exchange→MCP tool
discovery and the provisioning control plane. The framework adapters are not
blocked. They build their native object directly, and their constructor rows in
§8 of the ledger read **signature-confirmed offline**, except ADK's `gemini()`,
which is **live-verified**. An adapter refuses with `blocked(...)` only when the
installed framework version lacks the class or field it depends on.

---

## Error-taxonomy design (BG §1.2)

Turning the gateway's policy rejections into catchable, actionable exceptions is
the SDK's clearest value over raw HTTP. Two invariants govern the taxonomy in
`core/errors.py`:

1. **A policy refusal is never retried.** `PolicyViolation` (and its subclasses —
   `TokenBudgetExceeded`, `PIIDetected`, `PromptInjectionBlocked`,
   `ContentSafetyBlocked`) must be distinguishable from a transient error at the
   framework boundary, so a host framework never silently retries a governance
   refusal. The transport treats these as terminal.
2. **Every error carries a `remediation`.** On `PolicyViolation` the human-readable
   next step is a *required* field — the concrete action to take (e.g. "the budget
   window resets in 42m; request an increase in API Manager") is worth more than a
   stack trace. `AuthError` uses its class-level LLM-proxy guidance by default and
   canonical class-level overrides for connected-app and provider-chain failures,
   so its message and remediation always name the same credential plane and auth
   provider.

**`classify()` is fixture-driven, not guessed.** The HTTP-response → exception
mapping in `classify()` is populated from real rejection captures taken against a
live governed proxy (BG §1.5), not hand-written assumptions. The authoritative
discriminator is the error **`type`** plus specific headers — **not the status
code alone.** The captures established, for example, that:

- A **PII** block is a **403** with a *nested* error object whose `type` is
  `"pii_detected"` and **no** `www-authenticate` header — so it is decided *before*
  the generic 401/403→auth rule (`AuthError`). A 403 is not automatically an auth
  error.
- A **token-budget** rejection is a **429** with an *empty body*; the budget state
  lives entirely in headers (`x-token-reset` in ms), with no standard `retry-after`
  → `TokenBudgetExceeded`.
- An **upstream provider** rejection (e.g. OpenAI `model_not_found`) is a non-429
  4xx carrying the provider's nested `code`/`type`/`param`, passed through
  verbatim → `UpstreamRequestError` (terminal, but distinct from a policy refusal).
- A **5xx** is a retryable provider outage → `UpstreamModelError`.

A **prompt-injection** block is typed on its own signal: the
`x-injection-protection: blocked` response header decides `PromptInjectionBlocked`
*before* the generic 4xx / nested-error branch; its rejection *body* is now
LIVE-VERIFIED, a real 79-byte capture (#669). Only **content-moderation /
federated-guardrail** shapes remain under-documented, and those deliberately fall
through to a generic `PolicyViolation` whose message *says so* rather than
pretending to a precision the captures don't yet support — the same verification-discipline honesty
as the verification ledger. All errors subclass `DonkeyError`, which carries the
correlation/request IDs and the raw response for inspection.

---

## Adapter support depth (`BG §1.8`)

Not every framework gets the same CI guarantee, and the roster is deliberately
scoped rather than exhaustive. The former Tier 1 / Tier 2 split is **retired**
along with the eight-adapter roster (#197):

- **Deep — conformance-tested against the simulator:** LangGraph, and only
  LangGraph (alongside the raw client). Held to the conformance suite; the
  `ADAPTERS` registry marks it `conformance_tested=True`. Its `ChatOpenAI`
  constructor is signature-confirmed offline, not live-verified.
- **Supported at `connection_kwargs()`:** Google ADK, Strands, Microsoft Agent
  Framework, OpenAI Agents SDK, Anthropic SDK, CrewAI, LlamaIndex. Each
  constructor is **signature-confirmed offline** (`scripts/verify_frameworks.py`),
  except ADK's `gemini()`, which is **live-verified**. None is conformance-tested,
  and none is carried in the nightly framework matrix — a demoted framework returns with its own
  conformance run when demand justifies it.
- **Out of scope:** AutoGen and Semantic Kernel — Microsoft positions Agent
  Framework as their direct successor, so carrying all three would mean
  shipping two sunset-path adapters.

This makes `connection_kwargs()` *more* load-bearing, not less: it is the
entire supported surface for seven of the eight. A second framework is
promoted to deep support from demand evidence, one at a time — never guessed
up front. The demotion landed in #197; deepening LangGraph is tracked in #198.

**Conformance is how "supported" is proven, not asserted.** One suite
(`python/tests/conformance/suite.py`) runs identically against every adapter. A
framework is "supported" only when it passes every scenario **or** records an
*asserted exemption* in `KNOWN_LIMITATIONS` — never a silent skip. Those
exemptions are published in the README as credibility. CrewAI cannot
propagate a per-run correlation ID or populate `donkey.last_call`, because its
native OpenAI provider builds its own HTTP client; the adapter only hands it an
`interceptor` that keeps credentials to checked endpoints. ADK's `gemini()` (a
`Format=Gemini` proxy, #691) injects the shared client, so it records no
exemption. LlamaIndex, Microsoft Agent Framework and ADK's `model()` also send
through the shared client (`http_client` / `async_http_client`, an
`async_client`, and a pre-built OpenAI `client` for LiteLLM), so their
exemptions are gone (#740). Only CrewAI remains: its native OpenAI provider
builds the sync `OpenAI` and the `AsyncOpenAI` from one `client_params` dict,
and with an interceptor set it replaces `http_client` with its own `httpx`
client, so the SDK's async client cannot be injected.

The centre of gravity moves with the roster cut (`BG §1.5`): the internal
matrix shrinks to LangGraph, and the deliverable becomes the **customer-facing
pytest plugin** users run against their own agent (#191).

---

## Related documents

- [`spec/donkey-development-kit-build-plan.md`](spec/donkey-development-kit-build-plan.md) — the
  authoritative plan: phases, milestones, label taxonomy, standing invariants.
- [`spec/donkey-development-kit-build-guide.md`](spec/donkey-development-kit-build-guide.md) —
  feature-by-feature scope and acceptance bars; cited as `BG §N.N`.
- [`docs/verified-apis.md`](docs/verified-apis.md) — the verification ledger
  (source of truth for what is verified vs. blocked).
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — branch/PR/release flow, testing surfaces,
  coding conventions, and the docs-sync map. It is the canonical contributor
  guide; maintainers' optional AI-agent tooling is not part of this repository
  and never outranks it.
- `website/` — the consumer "how to use the SDK" documentation.

---

*"Agent Fabric", "Anypoint", and "Omni Gateway" are Salesforce trademarks; this
project is a descriptive, non-first-party SDK for consuming those capabilities
(the trademark/support boundary).*

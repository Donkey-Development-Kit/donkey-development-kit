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
core/           config · auth · transport · errors · budget · telemetry · cache · _verify  — ZERO framework deps (httpx + pydantic only)
```

**Not in the stack — `provisioning/`.** The declarative control plane
(specs · plan/diff/apply · governance lint · CLI) is on the build plan's
*Do not build* list — it must not compete with API Manager/Terraform — and its
endpoints are `_verify.blocked`. Treat it as legacy scaffolding: reachable, but
do not deepen it (see "Still blocked", below).

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

### How the pieces connect

- **`Donkey`** is the public surface and orchestrator. It owns one shared
  **`DonkeyAsyncClient`** (an `httpx.AsyncClient` subclass that injects the
  governance/attribution headers) and hands that single client to the LLM
  client, the registry, and every adapter — so there is exactly one transport and
  one header-injection point.
- **Adapters are lazy attributes.** `Donkey.__getattr__` resolves
  `donkey.<framework>` on first access through the `ADAPTERS` registry declared
  in `integrations/__init__.py`. Accessing an adapter whose optional extra is not
  installed raises an `ImportError` carrying the exact `pip install` command —
  never a bare `ModuleNotFoundError`. Each adapter returns the framework's own
  object (e.g. a real `langchain_openai.ChatOpenAI`), so there is nothing to
  unlearn and a three-line escape hatch (`connection_kwargs()`) out of the SDK.
- **Configuration** resolves in a fixed precedence — constructor kwargs → env
  vars → `.donkey-kit.toml` → default — and reports every missing field
  at once rather than one failure per run. `Donkey.from_env()` is the entry point.
- **The transport is the attachment point.** `DonkeyAsyncClient` exposes four
  internal lifecycle hooks — no-op by default, **not** public API, mirrored on the
  sync twin `DonkeyClient` — so the six-piece minimum *attaches* rather than
  re-wiring `send()` (`BG §1.1`, #179/#287). This is what makes the skeleton one
  milestone instead of six ad-hoc integrations:

  | Hook | When it fires | What attaches |
  | --- | --- | --- |
  | `_on_request` | once, before the retry loop | correlation ID + cost-tag headers (`BG §1.7`); OTel span **start** (`BG §1.6`) |
  | `_on_response` | once, on the final response (via `_finish()`) | `Budget` parse from `x-token-*` (`BG §1.3`); span **end**; classification |
  | `_on_refusal` | Phase-2 seam — no caller until `classify()` wires it (#181) | typed-refusal handlers (`BG §1.2`) |
  | `_swap_transport` | fixture seam | `simulate()` (#190) and `donkey mock` (#187) swap a fixture in (`BG §1.4`/`BG §1.5`) |

  Three contracts matter: **override the hook, not `send()`**; a subclass that
  overrides `_on_response` **must call `super()._on_response(...)`** or budget
  tracking silently breaks; and because a transport-level error escapes before
  `_finish` runs, a span opened in `_on_request` has **no paired `_on_response`**
  — span consumers must close in a `finally`, never relying on the response hook.
  A hookless client behaves exactly as it did before the hooks were added. The
  full contracts live in the `core/transport.py` docstrings.
- **`Governance`** (`governance.py`) is a second top-level object alongside
  `Donkey`, outside the linear import stack — it depends only on `core`. It is
  ONE object behind three verbs: `simulate()` (an ephemeral local
  gateway harness), `export()` (emit the governed-state manifest), and `resolve()`
  (reconcile a running `Donkey` against it, raising `GovernanceDrift` on
  mismatch); a separate platform-team-only `apply()` is the deliberate escape
  hatch. **All of these are currently `_verify.blocked`** — the `simulate()`
  harness included — pending the Verification milestone, so today the object is the
  shape, not yet the behaviour. Do not confuse it with `registry/governance.py`,
  which types governed-state *assets* one layer down.

Every governed surface ships in three ergonomic forms that must stay in lockstep:
the `donkey.<framework>` factory, a `connection_kwargs()` accessor, and a
module-level factory.

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
[`docs/verified-apis.md`](docs/verified-apis.md) to `VERIFIED`, **and** set
`verified=True` on its `Unverified(...)` entry in `core/_verify.py` so the warning
stops firing. `docs/verified-apis.md` is the single source of truth for what is
verified and the worklist of what is still blocked.

What is verified today: the LLM-proxy data plane (its base-URL shape — note there
is **no `/v1`** — the `client_id`/`client_secret` request-header pair, streaming,
and the live rejection shapes), the OAuth2 control-plane token path, and the
CLI-plugin REST contract (from static analysis). Still blocked: Exchange→MCP tool
discovery, the provisioning control plane, and the exact framework-adapter class
names/kwargs (the conformance kit and the build plan phases).

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
   stack trace.

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
*before* the generic 4xx / nested-error branch, even though its rejection *body*
is still pending live capture (#253). Only **content-moderation /
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

- **Deep — conformance-tested:** LangGraph, and only LangGraph. Held to the
  conformance suite; the `ADAPTERS` registry marks it `conformance_tested=True`.
- **Supported at `connection_kwargs()`:** Google ADK, Strands, Microsoft Agent
  Framework, OpenAI Agents SDK, Anthropic SDK, CrewAI, LlamaIndex. Verified at
  the kwargs level rather than the constructor level, and no longer carried in
  the nightly framework matrix — a demoted framework returns with its own
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
exemptions are published in the README as credibility (e.g. adapters that reach
models through LiteLLM cannot propagate a per-run correlation ID, because LiteLLM
owns the transport).

The centre of gravity moves with the roster cut (`BG §1.5`): the internal
matrix shrinks to LangGraph, and the deliverable becomes the **customer-facing
pytest plugin** users run against their own agent (#191).

Two adapters carry documented conformance exemptions: CrewAI (per-run
correlation degrades to per-client because it reaches models through LiteLLM,
like ADK) and the Anthropic SDK (depends on the proxy exposing an
Anthropic-native Messages API route, an open verification item).

---

## Related documents

- [`spec/donkey-development-kit-build-plan.md`](spec/donkey-development-kit-build-plan.md) — the
  authoritative plan: phases, milestones, label taxonomy, standing invariants.
- [`spec/donkey-development-kit-build-guide.md`](spec/donkey-development-kit-build-guide.md) —
  feature-by-feature scope and acceptance bars; cited as `BG §N.N`.
- [`docs/verified-apis.md`](docs/verified-apis.md) — the verification ledger
  (source of truth for what is verified vs. blocked).
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — branch/PR/release flow, testing surfaces,
  coding conventions.
- [`CLAUDE.md`](CLAUDE.md) — repo guidance for Claude Code: the invariants, the
  layer rule, and the skill index in operational form.
- [`.claude/skills/README.md`](.claude/skills/README.md) — the `ddk-*` skill
  index; the trigger-based path into the rules above (including
  `ddk-implementing-features`, the implement-stage skill).
- `website/` — the consumer "how to use the SDK" documentation.

---

*"Agent Fabric", "Anypoint", and "Omni Gateway" are Salesforce trademarks; this
project is a descriptive, non-first-party SDK for consuming those capabilities
(the trademark/support boundary).*

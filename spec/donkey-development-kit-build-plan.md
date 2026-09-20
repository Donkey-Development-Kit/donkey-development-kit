# Donkey Development Kit — Build Plan

**Status:** authoritative. This document and its companion
[`donkey-development-kit-build-guide.md`](donkey-development-kit-build-guide.md) are the
spec for what gets built and in what order.

**Date:** September 2026. Supersedes the retired v1 plan, whose three-pillar
model, provisioning control plane, and eight-adapter conformance roster are
cut.

## What this document is, and what the other two are

Three documents, one job each. Read them in this order:

| Document | Owns | Cite as |
|---|---|---|
| **This plan** | Phases, milestones, label taxonomy, implementation order, standing invariants | `Phase N`, or the section name |
| **[Build guide](donkey-development-kit-build-guide.md)** | Feature-by-feature scope: what each capability is, its scenario, its acceptance bar | `BG §1.1` … `BG §3.5` |

The per-issue detail is **not** in any of them. It is in the GitHub issues,
because the issue is the plan — see the backlog index below.

## Citation convention

Two active forms, kept textually distinct because their numbers would
otherwise collide (the layered-architecture rule versus the build guide's
LLM-client section):

- **`BG §1.1`** — a build guide section. Always prefixed `BG`. This is the
  form to use in all code, tests, issues, and commit messages that reference
  feature scope.
- **`Phase N`** — a milestone in this plan. Named, never numbered with `§`.

The five **standing invariants** below keep a bare `§` label — `§0.3`,
`§1.1`, `§2.1`, `§8.1`, `§8.4` — as their stable historical name. Those five
headings are the **only** bare `§` anchors that remain in the tree; the
~500 legacy citations that used to point into the retired v1 plan have been
migrated to `BG §N.N`, a named invariant, or a `Phase N` (issue #266). Cite
an invariant by its `§` label or its section name; do not introduce new bare
`§` numbers anywhere else.

## The product thesis

The SDK is not sold as a way to reach the Omni Gateway. Any stock OpenAI
client with a `base_url` and two headers does that, and reviewers will say
so. Built that way the wrapper is a dependency with no payoff.

The framing that determines what gets built (BG, *How the LLM wrapper
fits*):

> The wrapper is the **skeleton**. It is the *single point in the process*
> where every request enters and every response leaves — so it is the only
> place where budget headers, error classification, correlation IDs, cost
> tags, OTel spans, simulation, and refusal handlers can all attach without
> the developer wiring each one.

Two consequences that this plan is organised around:

1. **The skeleton is worth exactly the sum of what hangs on it.** Build the
   six-piece minimum and the wrapper is justified; ship the wrapper without
   them and it is not. This is why the six pieces are a single milestone and
   not spread across phases.
2. **The transport hooks come first.** Budget, spans, classification, and
   `simulate()` all attach at the same four lifecycle hooks. Building any of
   them before the hooks exist means building it twice.

**The six-piece minimum** is BG 1.2–1.7, labelled `six-piece-minimum`:
typed refusals · budget object + pacing · local gateway simulator ·
`simulate()` + conformance plugin · OTel GenAI instrumentation ·
correlation IDs + cost tags. BG 1.1 and 1.8–1.10 are the skeleton, the one
deep adapter, and the on-ramps that make the six pieces reachable.

## Phases and milestones

Seven milestones. Five are sequential product phases carrying a semver
target; two are standing and unversioned.

| Milestone | Issues | Scope |
|---|---|---|
| `Phase 1 — Build the MVP (0.1.0)` | 30 | Skeleton + the six-piece minimum (typed refusals, budget, simulator, simulate()+conformance, OTel GenAI, correlation/cost tags), one deep LangGraph adapter, decorators + CLI, docs, PyPI. Build guide 1.1-1.10. |
| `Phase 2 — Differentiate, go beyond (0.2.0)` | 21 | Refusal handlers, classification registry, HITL, identity helpers, in-repo scanner + Action, kill-switch, MCP discovery, second adapter, A2A serve/expose/dev. Build guide 2.1-2.9. |
| `Phase 3 — Platform capabilities (0.3.0)` | 6 | Policy handshake, to-the-code push, structured output + eval hooks. Largely gated on the Upstream gaps milestone. Build guide 3.2-3.4. |
| `Phase 4 — Enterprise readiness (0.4.0)` | 10 | Security review, performance budget, error-message pass, migration and deprecation policy, compliance evidence, log shipping, residency, workload identity, support model. |
| `Phase 5 — Complete rollout (1.0.0)` | 6 | TypeScript port, remaining adapters by demand, go-to-market, 1.0 stability guarantees. Build guide 3.5, gated on Python PMF. |
| `Verification` | 6 | The verification-discipline worklist: never invent an endpoint, header, class name or kwarg. Cross-phase, unversioned — each row blocks specific feature work. |
| `Upstream gaps` | 6 | Product asks to the Omni Gateway team. Filed in Phase 1, landing whenever the gateway ships them. Build guide 3.1. |

`Verification` and `Upstream gaps` do not complete and are not versioned.
They run continuously and each of their rows blocks specific feature work,
which is the point: a verification question started when the phase that
needs it starts will block that phase. Started early, it resolves in time.

## Standing invariants

These survive the strategy change unaltered and several are enforced in
CI. They are stated here because the document that used to own them is now
archived; the `§` anchors resolve into that archive.

### Verification discipline (`§0.3`) — the most important rule

**Never invent an endpoint, header name, class name, or kwarg.** A
fabricated endpoint that 404s in a customer sandbox destroys trust in the
whole package. Two mechanisms in `core/_verify.py` enforce it:

- `_verify.blocked("…")` returns
  `NotImplementedError("blocked on verification: …")`, used where there is
  no defensible placeholder at all. **Do not replace these with guesses.**
- `Unverified(...)` placeholders emit a one-time `UnverifiedValueWarning`
  when read and are overridable via config/env. A value flips to
  `verified=True` only after confirmation against a real sandbox.

`docs/verified-apis.md` is the single source of truth for what is verified.
When you confirm a value: flip its row there **and** set `verified=True` in
`_verify.py`. The `Verification` milestone is the worklist.

### Layered architecture (`§1.1`) — enforced by `lint-imports`

Lower layers must never import higher ones; violating it fails CI.

```
integrations  (top — per-framework native-object adapters)
     ↓
tools         (MCP discovery/binding)
     ↓
registry      (Exchange/governed-state)
     ↓
llm           (framework-free OpenAI-compatible proxy client)
     ↓
core          (config, auth, transport, errors, telemetry, cache)
```

**`core/` has zero agent-framework dependencies — httpx + pydantic only.**
The `base-only` CI job installs only the base package and imports
`donkey_kit` to catch accidental top-level framework imports. Adapters
import their framework **lazily inside methods**, never at module top
level. The adapter cut makes this gate more important, not less: with one
conformance-gated adapter and seven `connection_kwargs()`-only frameworks,
nothing but the linter stops a framework import from drifting into `core`.

### Config resolution (`§2.1`)

kwargs → env vars → `.donkey-kit.toml` → default. Missing fields are
reported **all at once**, not one exception per field. `Donkey.from_env()`
is the entry point.

### Conformance (`§8.1`)

One suite, run identically against every supported adapter. A framework is
"supported" only when it passes every scenario or records an **asserted
exemption** in `KNOWN_LIMITATIONS` — never a silent skip. Exemptions are
published in the README as credibility.

What changes under this plan: the suite's centre of gravity moves from an
internal eight-adapter matrix to the **customer-facing pytest plugin**
(BG 1.5) that users run against their own agent. LangGraph is the one
conformance-gated adapter (BG 1.8).

### Extras are floors, never ceilings (`§8.4`)

No upper pins in `pyproject.toml`. A fresh resolve always takes the newest
release so the nightly matrix finds breakage early. Known incompatibilities
are documented in `docs/verified-apis.md` as local dev constraints, **not**
encoded as pins.

## Do not build, at any phase

From the build guide's closing summary. These are refusals, not backlog:

- Client-side policy enforcement — the gateway is the enforcement point.
- Client-side semantic caching.
- A provisioning control plane competing with API Manager or Terraform.
- Re-implementations of Agent Scanners, Kill Switch, or Trusted Agent
  Identity.
- An approval UI or queue.
- An eval framework.
- The gateway inside the agent process.
- Our own A2A protocol implementation — wrap the official `a2a-sdk`.

The SDK's job at each of these boundaries is to make the platform's own
capability reachable and typed, not to reproduce it.

## Implementation order

Waves are sequential; issues **inside** a wave can run in parallel.

**Phase 1 critical path:** [#179](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/179) `P1-01` → [#181](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/181) `P1-03` → [#187](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/187) `P1-09` → [#190](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/190) `P1-13` → [#191](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/191) `P1-15` → [#199](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/199) `P1-26` → [#206](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/206) `P1-35`

Everything else in Phase 1 branches off that spine. If a wave slips, this
is the chain that moves the phase-exit date.

### Wave 0 — before anything else

Two of these are pure scope reduction and one is the attachment point every other Phase 1 issue plugs into. Doing them first makes every later issue smaller. Nothing in Phase 1 should start ahead of P1-01.

- [#207](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/207) **meta: replace the build plan with the phase-based plan and realign CLAUDE.md and the skills**  
  Realign the spec, CLAUDE.md and the skills before any issue is worked against them — otherwise every PR in wave 1 is reviewed against a spec that describes the cut architecture.
- [#197](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/197) **adapters: demote seven adapters to connection_kwargs-only and cut the conformance roster to LangGraph**  
  Cut the adapter roster first. Every day the eight-adapter matrix stays in blocking CI is a day of maintenance spent on frameworks the plan has already demoted.
- [#179](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/179) **transport: add the four lifecycle hooks every Phase 1 feature attaches to**  
  The four lifecycle hooks. Budget, spans, classification and simulate() all attach here — building any of them before the hooks exist means building them twice.
- [#247](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/247) **upstream: 429 carries no Retry-After, only x-token-reset**  
  File all six upstream gaps on day one. They have long lead times and none of them is closable by writing SDK code.
- [#248](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/248) **upstream: no budget-query endpoint — remaining budget is only visible in-band**  
  Gap #2 is what would make the Budget object live instead of stale.
- [#249](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/249) **upstream: no policy-discovery endpoint — clients cannot ask what is in force**  
  Gap #3 gates almost all of Phase 3 — the earliest possible filing is the only lever available.
- [#250](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/250) **upstream: no dry-run mode — policy cannot be evaluated without blocking**  
  Gap #4 is the only route to testing against real policy config.
- [#251](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/251) **upstream: guardrail-verdict error contract is under-documented**  
  Gap #5 is what turns the guardrail fall-through into a typed error.
- [#252](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/252) **upstream: no outbound-connect or tunnel path for A2A ingress**  
  Gap #6 is the one that makes A2A publishing feel like magic.

### Wave 1 — the contract, and the thing that replays it

The fixtures are one artefact shared by classify() and the simulator. Build them together or they drift immediately. V-01 comes first because the fixtures cite it.

- [#253](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/253) **verify: re-confirm the v1.11-v1.13 rejection contracts against current docs and a sandbox**  
  Re-confirm the v1.11-v1.13 contracts. The fixtures record this docs version, so verifying after capture means recapturing.
- [#181](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/181) **errors: re-align classify() to the documented v1.11-v1.13 rejection contracts**  
  Re-align classify() to the documented contracts.
- [#187](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/187) **simulator: donkey mock — a local HTTP server that replays real rejection fixtures**  
  The simulator, sharing exactly those fixtures.
- [#180](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/180) **donkey: Donkey.openai() returns a native openai.AsyncOpenAI**  
  Donkey.openai(). Small, unblocks anyone writing examples.
- [#206](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/206) **release: publish to PyPI with semver and a changelog**  
  Set up the release pipeline now and publish dev releases from the start — the strategy report rates PyPI five stars and 'do first'. The 0.1.0 tag waits for the phase exit, the plumbing should not.

### Wave 2 — the six pieces, in parallel tracks

Four independent tracks, each attaching to a different hook. This is where most of Phase 1's effort sits and where parallelism actually pays.

- [#182](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/182) **errors: require a non-empty remediation on every PolicyViolation**  
  Refusals track: mandatory remediation.
- [#183](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/183) **errors: prove policy refusals are terminal and openai's built-in 429 retry never fires**  
  Refusals track: prove the 429 retry never fires. Highest-value single test in the phase.
- [#184](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/184) **errors: honest fall-through for guardrail verdicts whose shape is unconfirmed**  
  Refusals track: honest guardrail fall-through.
- [#185](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/185) **budget: Budget as a first-class object parsed from x-token-* headers**  
  Budget track: the Budget object.
- [#186](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/186) **budget: wait_for_reset() and pace(reserve=) raising BudgetReserveReached**  
  Budget track: pace() and wait_for_reset(). Needs P1-07.
- [#192](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/192) **telemetry: OTel GenAI spans at a pinned semconv version, dual-emitted with a stable donkey.* namespace**  
  Telemetry track: pinned semconv + dual-emit.
- [#195](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/195) **telemetry: per-call and per-run correlation IDs via donkey.run()**  
  Telemetry track: correlation IDs and node propagation.
- [#196](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/196) **telemetry: validated cost-attribution tags (team, project, env, enduser.id)**  
  Telemetry track: validated cost tags.
- [#188](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/188) **simulator: scenario scripting for PII, budget and injection**  
  Simulator track: scenario scripting. Needs P1-09.
- [#189](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/189) **simulator: the honesty guarantee — x-donkey-simulator header and a stock-client test**  
  Simulator track: the honesty guarantee. Needs P1-09.

### Wave 3 — what the six pieces make possible

Each of these needs at least one wave-2 track finished.

- [#190](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/190) **testing: donkey.simulate() — in-process refusal injection with no server**  
  simulate(). Needs the swappable _transport and the fixtures.
- [#193](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/193) **telemetry: a refused request still produces a span, and streaming produces exactly one**  
  Refusal and streaming span lifecycle. Needs P1-17 and P1-03.
- [#194](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/194) **telemetry: zero-config OTLP export, one-flag opt-out, <1ms overhead benchmarked in CI**  
  Zero-config OTLP export and the overhead benchmark. Needs P1-17.
- [#198](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/198) **adapter/langgraph: deepen to the one conformance-gated adapter**  
  Deepen the LangGraph adapter. Needs P1-21 for propagation.
- [#200](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/200) **decorators: @donkey.governed and @donkey.tool**  
  Decorators. Needs P1-21 for the run scope, and @donkey.tool is read later by the scanner and the A2A card generator.

### Wave 4 — the surfaces a user actually touches

Everything here composes earlier work into something installable.

- [#191](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/191) **testing: the pytest plugin and the conformance suite customers run against their own agent**  
  The conformance suite. Needs simulate(), the simulator and scenario scripting.
- [#202](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/202) **cli: donkey doctor — tell wrong credentials from wrong URL from model-not-allowed**  
  donkey doctor. Needs the remediation strings and the Budget object.
- [#201](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/201) **cli: the donkey command surface — init, plus consistent flags and exit codes across all four**  
  The CLI surface. Needs mock and test to exist first.

### Wave 5 — prove it, document it, ship it

The phase-exit artefacts. The demo is the integration test for everything above it, so it should fail loudly while any wave-4 item is incomplete.

- [#199](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/199) **demos: langgraph-support-triage implements Scenario A end-to-end against the simulator**  
  Scenario A demo end-to-end against the simulator.
- [#203](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/203) **docs: a 15-minute quickstart that needs no gateway**  
  The 15-minute quickstart.
- [#204](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/204) **docs: scenario pages, the refusal cookbook, and per-framework connection_kwargs pages**  
  Scenario pages, refusal cookbook, framework pages.
- [#205](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/205) **docs: publish llms.txt so coding assistants can read the SDK docs**  
  llms.txt.
- [#259](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/259) **epic: Phase 1 — Build the MVP**  
  Close the Phase 1 epic against its exit criteria — including the 500-star / 3-contributor reassessment gate.

### Run continuously through Phase 1 — verification

These gate Phase 2 features. Started when Phase 2 starts, they block it; started now, they resolve in time. This is the single most common way a phase plan slips.

- [#254](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/254) **verify: the A2A proxy provisioning API, or confirm there is no supported path**  
  A2A proxy provisioning — gates P2-19 expose.
- [#255](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/255) **verify: the Trusted Agent Identity token-exchange endpoint and header**  
  TAI token exchange — gates P2-08 identity and P2-07 step-up.
- [#256](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/256) **verify: the Agent Kill Switch rejection shape**  
  Kill-switch rejection shape — gates P2-13.
- [#257](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/257) **verify: whether Anypoint Monitoring or Agent Visualizer ingests OTLP GenAI spans**  
  Whether the platform ingests OTLP GenAI — informs what 1.6 may claim.
- [#258](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/258) **verify: self-managed gateway image availability and licensing for local development**  
  Self-managed gateway licensing — decides whether donkey dev Option A is ever attempted.

### Phase 2, wave 1 — nothing external blocking

Start with the work that needs no verification answer, so Phase 2 has momentum while the verification results land.

- [#208](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/208) **refusals: declarative reaction handlers registered once, applied at the transport**  
  Refusal handlers. Needs _on_refusal and the taxonomy.
- [#209](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/209) **refusals: enforce that a handler can react but never un-refuse**  
  The cannot-un-refuse rule. Ship with P2-01, not after.
- [#210](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/210) **classify: a registry so custom gateway policies become typed exceptions**  
  Classification registry.
- [#217](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/217) **scanner: donkey scan walks the repo and writes donkey.yaml + agent-card.json**  
  donkey scan. Needs @donkey.tool from P1-27.
- [#224](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/224) **a2a: donkey.serve() — an A2A listener in one line, wrapping the official a2a-sdk**  
  donkey serve. Needs the decorators; wraps the official a2a-sdk.
- [#223](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/223) **adapters: a second deep adapter, chosen by Phase 1 demand**  
  Choose the second adapter — but only once Phase 1 produced demand evidence. Do not guess.

### Phase 2, wave 2 — builds on wave 1

- [#225](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/225) **a2a: governance-aware inbound — correlation, tags, spans and typed refusals on served tasks**  
  Governed A2A inbound. Needs P2-17.
- [#211](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/211) **hitl: one vocabulary for pause-and-ask-a-human**  
  HITL vocabulary. Needs the decorators.
- [#212](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/212) **hitl: map the vocabulary onto LangGraph interrupt() and Command(resume=)**  
  LangGraph interrupt() mapping. Needs P2-04 and P1-25.
- [#218](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/218) **scanner: donkey publish registers the manifest with Exchange / Agent Registry**  
  donkey publish. Needs P2-10 and the Exchange publish verification.
- [#220](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/220) **killswitch: recognise the kill-switch rejection and stop cleanly**  
  Kill-switch awareness. Small once V-04 lands.
- [#221](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/221) **tools: donkey.tools.discover() returns governed MCP tools as native framework tools**  
  MCP discovery. Needs the discovery-endpoint verification.

### Phase 2, wave 3 — the remainder

- [#219](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/219) **scanner: the scan-and-publish GitHub Action**  
  The GitHub Action. Needs scan and publish.
- [#222](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/222) **tools: allow/deny filtering and descriptor token accounting**  
  Tool filtering and descriptor accounting. Needs P2-14.
- [#215](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/215) **identity: donkey.as_user() — RFC 8693 token exchange and header placement**  
  donkey.as_user(). Needs V-03.
- [#216](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/216) **identity: a missing or expired user token fails typed, never falls back to service identity**  
  No silent downgrade to service identity. Ship with P2-08.
- [#214](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/214) **hitl: route escalation through gateway step-up (Trusted Agent Identity)**  
  Gateway step-up routing. Needs V-03 and P2-04.
- [#226](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/226) **a2a: donkey expose — provision the A2A proxy, policies and registry entry in one command**  
  donkey expose. Needs V-02.
- [#227](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/227) **a2a: donkey dev — a gateway in front of the laptop, real or simulated**  
  donkey dev. Option B ships regardless; Option A needs V-06.
- [#213](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/213) **hitl: mappings for OpenAI Agents, ADK, Strands and MCP elicitation**  
  HITL for the other frameworks. Sequence behind P2-16 rather than re-deepening four adapters speculatively.
- [#260](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/260) **epic: Phase 2 — Differentiate, go beyond**  
  Close the Phase 2 epic against its exit criteria.

### Phase 3 — gated externally, not by capacity

Only P3-04 and P3-05 are independently schedulable. The rest waits on upstream gap #3, which is not this team's decision.

- [#231](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/231) **llm: keep the .parse() structured-output path governed, and support it in the simulator**  
  Structured output on the .parse() path. Schedulable now.
- [#232](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/232) **telemetry: evaluation hooks that attach a score to the run span**  
  Evaluation hooks. Schedulable now.
- [#228](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/228) **policies: the policy handshake client — read the in-force policy set**  
  Policy handshake. Blocked on UG-3.
- [#229](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/229) **policies: advisory only — the gateway always wins, and the client learns from the refusal**  
  The advisory-only rule. Ship with P3-01.
- [#230](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/230) **policies: to-the-code push — refresh at run boundaries, never mid-run**  
  To-the-code push. Needs P3-01.
- [#261](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/261) **epic: Phase 3 — Platform capabilities**  
  Close the Phase 3 epic.

### Phase 4 — before 1.0

Security and the API contract gate the release; the priority/p3 items are genuinely optional and can be dropped if the phase runs long.

- [#233](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/233) **security: independent security review and supply-chain hardening**  
  Security review and supply-chain hardening. Start early — findings take time to resolve.
- [#236](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/236) **docs: migration guide, deprecation policy, and the public API contract**  
  Public API contract and deprecation policy. Everything after this is bound by it, so it should not be last.
- [#234](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/234) **perf: a latency and overhead budget enforced in CI**  
  Performance budget in CI.
- [#235](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/235) **errors: a full pass over every remediation string and error message**  
  Error-message and remediation pass.
- [#237](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/237) **compliance: map correlation and span data to EU AI Act Art. 12 and ISO 42001 evidence**  
  Compliance evidence mapping.
- [#241](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/241) **ops: support model, issue templates and release cadence**  
  Support model, templates, cadence.
- [#238](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/238) **telemetry: structured log shipping to the control plane**  
  Log shipping. Optional.
- [#239](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/239) **config: data-residency and region routing hints**  
  Residency routing. Optional.
- [#240](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/240) **identity: evaluate workload identity (SPIFFE / Entra Agent ID) for agent-to-gateway auth**  
  Workload identity spike. Optional; output is a recommendation.
- [#262](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/262) **epic: Phase 4 — Enterprise readiness**  
  Close the Phase 4 epic.

### Phase 5 — after product-market fit

The TypeScript gate is a hard one: Phase 1 exit met AND Phase 2 production evidence. Starting early means fixing every gateway change twice.

- [#242](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/242) **typescript: port the six-piece minimum**  
  TypeScript port of the six pieces. Confirm the gate on the issue before any work starts.
- [#243](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/243) **typescript: shared conformance scenarios across Python and TypeScript**  
  Shared conformance scenarios across both languages.
- [#244](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/244) **adapters: bring back remaining frameworks by demand, each to the full bar**  
  Remaining adapters, by demand, one at a time.
- [#245](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/245) **gtm: framework-docs partner PRs and launch channels**  
  Framework-docs partner PRs and launch.
- [#246](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/246) **release: 1.0.0 with stability guarantees**  
  1.0.0 with stability guarantees.
- [#263](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/263) **epic: Phase 5 — Complete rollout**  
  Close the Phase 5 epic.

## Backlog index

The authoritative detail for every item below is in its GitHub issue.

### Phase 1 — Build the MVP (0.1.0) (30)

- [#179](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/179) transport: add the four lifecycle hooks every Phase 1 feature attaches to  
  `enhancement` `area:skeleton` `priority/p0` `size/m`
- [#180](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/180) donkey: Donkey.openai() returns a native openai.AsyncOpenAI  
  `enhancement` `area:skeleton` `priority/p1` `size/s`
- [#181](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/181) errors: re-align classify() to the documented v1.11-v1.13 rejection contracts  
  `enhancement` `area:refusals` `six-piece-minimum` `priority/p0` `size/m`
- [#182](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/182) errors: require a non-empty remediation on every PolicyViolation  
  `enhancement` `area:refusals` `six-piece-minimum` `priority/p1` `size/s`
- [#183](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/183) errors: prove policy refusals are terminal and openai's built-in 429 retry never fires  
  `enhancement` `area:refusals` `six-piece-minimum` `priority/p0` `size/s`
- [#184](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/184) errors: honest fall-through for guardrail verdicts whose shape is unconfirmed  
  `enhancement` `area:refusals` `blocked-on-verification` `priority/p2` `size/s`
- [#185](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/185) budget: Budget as a first-class object parsed from x-token-* headers  
  `enhancement` `area:budget` `six-piece-minimum` `priority/p0` `size/s`
- [#186](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/186) budget: wait_for_reset() and pace(reserve=) raising BudgetReserveReached  
  `enhancement` `area:budget` `six-piece-minimum` `priority/p0` `size/m`
- [#187](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/187) simulator: donkey mock — a local HTTP server that replays real rejection fixtures  
  `enhancement` `area:simulator` `six-piece-minimum` `priority/p0` `size/l`
- [#188](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/188) simulator: scenario scripting for PII, budget and injection  
  `enhancement` `area:simulator` `priority/p1` `size/m`
- [#189](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/189) simulator: the honesty guarantee — x-donkey-simulator header and a stock-client test  
  `enhancement` `area:simulator` `priority/p1` `size/s`
- [#190](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/190) testing: donkey.simulate() — in-process refusal injection with no server  
  `enhancement` `area:testing` `six-piece-minimum` `priority/p0` `size/m`
- [#191](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/191) testing: the pytest plugin and the conformance suite customers run against their own agent  
  `enhancement` `area:testing` `six-piece-minimum` `priority/p0` `size/l`
- [#192](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/192) telemetry: OTel GenAI spans at a pinned semconv version, dual-emitted with a stable donkey.* namespace  
  `enhancement` `area:telemetry` `six-piece-minimum` `priority/p0` `size/m`
- [#193](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/193) telemetry: a refused request still produces a span, and streaming produces exactly one  
  `enhancement` `area:telemetry` `six-piece-minimum` `priority/p1` `size/m`
- [#194](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/194) telemetry: zero-config OTLP export, one-flag opt-out, <1ms overhead benchmarked in CI  
  `enhancement` `area:telemetry` `priority/p1` `size/m`
- [#195](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/195) telemetry: per-call and per-run correlation IDs via donkey.run()  
  `enhancement` `area:telemetry` `six-piece-minimum` `priority/p0` `size/m`
- [#196](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/196) telemetry: validated cost-attribution tags (team, project, env, enduser.id)  
  `enhancement` `area:telemetry` `six-piece-minimum` `priority/p1` `size/s`
- [#197](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/197) adapters: demote seven adapters to connection_kwargs-only and cut the conformance roster to LangGraph  
  `chore` `area:adapters` `priority/p0` `size/m`
- [#198](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/198) adapter/langgraph: deepen to the one conformance-gated adapter  
  `enhancement` `area:adapters` `priority/p0` `size/m`
- [#199](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/199) demos: langgraph-support-triage implements Scenario A end-to-end against the simulator  
  `documentation` `area:docs` `area:adapters` `priority/p1` `size/m`
- [#200](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/200) decorators: @donkey.governed and @donkey.tool  
  `enhancement` `area:cli` `priority/p1` `size/s`
- [#201](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/201) cli: the donkey command surface — init, plus consistent flags and exit codes across all four  
  `enhancement` `area:cli` `priority/p2` `size/m`
- [#202](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/202) cli: donkey doctor — tell wrong credentials from wrong URL from model-not-allowed  
  `enhancement` `area:cli` `priority/p1` `size/m`
- [#203](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/203) docs: a 15-minute quickstart that needs no gateway  
  `documentation` `area:docs` `priority/p0` `size/m`
- [#204](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/204) docs: scenario pages, the refusal cookbook, and per-framework connection_kwargs pages  
  `documentation` `area:docs` `area:adapters` `priority/p1` `size/m`
- [#205](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/205) docs: publish llms.txt so coding assistants can read the SDK docs  
  `documentation` `area:docs` `priority/p2` `size/s`
- [#206](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/206) release: publish to PyPI with semver and a changelog  
  `chore` `area:release` `priority/p0` `size/m`
- [#207](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/207) meta: replace the build plan with the phase-based plan and realign CLAUDE.md and the skills  
  `chore` `area:docs` `priority/p0` `size/m`
- [#259](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/259) epic: Phase 1 — Build the MVP  
  `epic` `priority/p0` `size/xl`

### Phase 2 — Differentiate, go beyond (0.2.0) (21)

- [#208](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/208) refusals: declarative reaction handlers registered once, applied at the transport  
  `enhancement` `area:refusals` `priority/p0` `size/m`
- [#209](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/209) refusals: enforce that a handler can react but never un-refuse  
  `enhancement` `area:refusals` `priority/p0` `size/s`
- [#210](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/210) classify: a registry so custom gateway policies become typed exceptions  
  `enhancement` `area:refusals` `priority/p1` `size/m`
- [#211](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/211) hitl: one vocabulary for pause-and-ask-a-human  
  `enhancement` `area:hitl` `priority/p1` `size/m`
- [#212](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/212) hitl: map the vocabulary onto LangGraph interrupt() and Command(resume=)  
  `enhancement` `area:hitl` `area:adapters` `priority/p1` `size/m`
- [#213](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/213) hitl: mappings for OpenAI Agents, ADK, Strands and MCP elicitation  
  `enhancement` `area:hitl` `priority/p2` `size/l`
- [#214](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/214) hitl: route escalation through gateway step-up (Trusted Agent Identity)  
  `enhancement` `area:hitl` `area:identity` `blocked-on-verification` `priority/p2` `size/m`
- [#215](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/215) identity: donkey.as_user() — RFC 8693 token exchange and header placement  
  `enhancement` `area:identity` `blocked-on-verification` `priority/p1` `size/m`
- [#216](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/216) identity: a missing or expired user token fails typed, never falls back to service identity  
  `enhancement` `area:identity` `priority/p1` `size/s`
- [#217](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/217) scanner: donkey scan walks the repo and writes donkey.yaml + agent-card.json  
  `enhancement` `area:scanner` `priority/p1` `size/l`
- [#218](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/218) scanner: donkey publish registers the manifest with Exchange / Agent Registry  
  `enhancement` `area:scanner` `blocked-on-verification` `priority/p1` `size/m`
- [#219](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/219) scanner: the scan-and-publish GitHub Action  
  `enhancement` `area:scanner` `priority/p2` `size/m`
- [#220](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/220) killswitch: recognise the kill-switch rejection and stop cleanly  
  `enhancement` `area:refusals` `blocked-on-verification` `priority/p1` `size/s`
- [#221](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/221) tools: donkey.tools.discover() returns governed MCP tools as native framework tools  
  `enhancement` `area:tools` `blocked-on-verification` `priority/p1` `size/l`
- [#222](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/222) tools: allow/deny filtering and descriptor token accounting  
  `enhancement` `area:tools` `priority/p2` `size/m`
- [#223](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/223) adapters: a second deep adapter, chosen by Phase 1 demand  
  `enhancement` `area:adapters` `priority/p2` `size/m`
- [#224](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/224) a2a: donkey.serve() — an A2A listener in one line, wrapping the official a2a-sdk  
  `enhancement` `area:a2a` `priority/p1` `size/m`
- [#225](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/225) a2a: governance-aware inbound — correlation, tags, spans and typed refusals on served tasks  
  `enhancement` `area:a2a` `area:telemetry` `priority/p1` `size/m`
- [#226](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/226) a2a: donkey expose — provision the A2A proxy, policies and registry entry in one command  
  `enhancement` `area:a2a` `blocked-on-verification` `priority/p2` `size/l`
- [#227](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/227) a2a: donkey dev — a gateway in front of the laptop, real or simulated  
  `enhancement` `area:a2a` `area:simulator` `blocked-on-verification` `priority/p2` `size/l`
- [#260](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/260) epic: Phase 2 — Differentiate, go beyond  
  `epic` `priority/p1` `size/xl`

### Phase 3 — Platform capabilities (0.3.0) (6)

- [#228](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/228) policies: the policy handshake client — read the in-force policy set  
  `enhancement` `area:policies` `blocked-on-verification` `priority/p1` `size/l`
- [#229](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/229) policies: advisory only — the gateway always wins, and the client learns from the refusal  
  `enhancement` `area:policies` `priority/p0` `size/s`
- [#230](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/230) policies: to-the-code push — refresh at run boundaries, never mid-run  
  `enhancement` `area:policies` `priority/p2` `size/l`
- [#231](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/231) llm: keep the .parse() structured-output path governed, and support it in the simulator  
  `enhancement` `area:skeleton` `priority/p2` `size/s`
- [#232](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/232) telemetry: evaluation hooks that attach a score to the run span  
  `enhancement` `area:telemetry` `priority/p3` `size/m`
- [#261](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/261) epic: Phase 3 — Platform capabilities  
  `epic` `priority/p2` `size/l`

### Phase 4 — Enterprise readiness (0.4.0) (10)

- [#233](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/233) security: independent security review and supply-chain hardening  
  `chore` `area:release` `priority/p0` `size/l`
- [#234](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/234) perf: a latency and overhead budget enforced in CI  
  `chore` `area:release` `priority/p1` `size/m`
- [#235](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/235) errors: a full pass over every remediation string and error message  
  `enhancement` `area:refusals` `priority/p2` `size/m`
- [#236](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/236) docs: migration guide, deprecation policy, and the public API contract  
  `documentation` `area:docs` `area:release` `priority/p1` `size/m`
- [#237](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/237) compliance: map correlation and span data to EU AI Act Art. 12 and ISO 42001 evidence  
  `documentation` `area:docs` `area:telemetry` `priority/p2` `size/m`
- [#238](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/238) telemetry: structured log shipping to the control plane  
  `enhancement` `area:telemetry` `priority/p3` `size/m`
- [#239](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/239) config: data-residency and region routing hints  
  `enhancement` `area:skeleton` `blocked-on-verification` `priority/p3` `size/s`
- [#240](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/240) identity: evaluate workload identity (SPIFFE / Entra Agent ID) for agent-to-gateway auth  
  `enhancement` `area:identity` `priority/p3` `size/m`
- [#241](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/241) ops: support model, issue templates and release cadence  
  `chore` `area:release` `priority/p2` `size/s`
- [#262](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/262) epic: Phase 4 — Enterprise readiness  
  `epic` `priority/p2` `size/l`

### Phase 5 — Complete rollout (1.0.0) (6)

- [#242](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/242) typescript: port the six-piece minimum  
  `enhancement` `area:typescript` `priority/p1` `size/xl`
- [#243](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/243) typescript: shared conformance scenarios across Python and TypeScript  
  `enhancement` `area:typescript` `area:testing` `priority/p2` `size/l`
- [#244](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/244) adapters: bring back remaining frameworks by demand, each to the full bar  
  `enhancement` `area:adapters` `priority/p3` `size/l`
- [#245](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/245) gtm: framework-docs partner PRs and launch channels  
  `documentation` `area:docs` `priority/p3` `size/m`
- [#246](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/246) release: 1.0.0 with stability guarantees  
  `chore` `area:release` `priority/p1` `size/m`
- [#263](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/263) epic: Phase 5 — Complete rollout  
  `epic` `priority/p3` `size/xl`

### Verification (6)

- [#253](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/253) verify: re-confirm the v1.11-v1.13 rejection contracts against current docs and a sandbox  
  `blocked-on-verification` `area:refusals` `priority/p0` `size/s`
- [#254](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/254) verify: the A2A proxy provisioning API, or confirm there is no supported path  
  `blocked-on-verification` `area:a2a` `priority/p1` `size/m`
- [#255](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/255) verify: the Trusted Agent Identity token-exchange endpoint and header  
  `blocked-on-verification` `area:identity` `priority/p1` `size/s`
- [#256](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/256) verify: the Agent Kill Switch rejection shape  
  `blocked-on-verification` `area:refusals` `priority/p1` `size/s`
- [#257](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/257) verify: whether Anypoint Monitoring or Agent Visualizer ingests OTLP GenAI spans  
  `blocked-on-verification` `area:telemetry` `priority/p2` `size/s`
- [#258](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/258) verify: self-managed gateway image availability and licensing for local development  
  `blocked-on-verification` `area:a2a` `area:simulator` `priority/p2` `size/s`

### Upstream gaps (6)

- [#247](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/247) upstream: 429 carries no Retry-After, only x-token-reset  
  `upstream-gap` `area:budget` `priority/p1` `size/s`
- [#248](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/248) upstream: no budget-query endpoint — remaining budget is only visible in-band  
  `upstream-gap` `area:budget` `priority/p1` `size/s`
- [#249](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/249) upstream: no policy-discovery endpoint — clients cannot ask what is in force  
  `upstream-gap` `area:policies` `priority/p1` `size/m`
- [#250](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/250) upstream: no dry-run mode — policy cannot be evaluated without blocking  
  `upstream-gap` `area:testing` `priority/p2` `size/m`
- [#251](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/251) upstream: guardrail-verdict error contract is under-documented  
  `upstream-gap` `area:refusals` `priority/p1` `size/m`
- [#252](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/252) upstream: no outbound-connect or tunnel path for A2A ingress  
  `upstream-gap` `area:a2a` `priority/p2` `size/l`

## Label taxonomy

### Area

Each area maps to a build guide section, so an issue's area label tells you
which part of the guide is its spec.

| Label | Meaning |
|---|---|
| `area:skeleton` | DonkeyAsyncClient, config, transport hooks, Donkey facade (1.1) |
| `area:refusals` | classify(), the typed error taxonomy, handlers (1.2, 2.1, 2.2) |
| `area:budget` | Budget object, pacing, rate-limit awareness (1.3) |
| `area:simulator` | donkey mock — the local gateway simulator (1.4) |
| `area:testing` | simulate(), the pytest plugin, conformance kit (1.5) |
| `area:telemetry` | OTel GenAI spans, correlation IDs, cost tags (1.6, 1.7) |
| `area:adapters` | Framework adapters and connection_kwargs() (1.8, 2.8) |
| `area:cli` | The donkey CLI and decorators (1.9) |
| `area:docs` | Docs site, README, llms.txt, examples (1.10) |
| `area:hitl` | Human-in-the-loop normalisation and approval routing (2.3) |
| `area:identity` | On-behalf-of, RFC 8693 token exchange (2.4) |
| `area:scanner` | donkey scan / publish and the GitHub Action (2.5) |
| `area:tools` | MCP tool discovery and governed tool consumption (2.7) |
| `area:a2a` | A2A serve / expose / dev (2.9) |
| `area:policies` | Policy handshake and to-the-code push (3.2, 3.3) |
| `area:release` | Packaging, PyPI, semver, changelog, CI |
| `area:typescript` | TypeScript port (3.5) |

### Status

| Label | Meaning |
|---|---|
| `blocked-on-verification` | Cannot be closed by writing code (verification discipline) |
| `upstream-gap` | A product ask to the Omni Gateway team, not SDK code |
| `epic` | Tracking issue spanning several child issues |
| `six-piece-minimum` | Part of the minimum set that justifies installing the SDK |

Plus type (`enhancement` / `bug` / `documentation` / `chore`),
`priority/p0`–`p3`, and `size/xs`–`xl`.

The old `area/*` (slash) labels, the `fw:*` per-framework set, `roadmap`,
`built`, `tier-1`/`tier-2`, and `python`/`typescript` are **retired**. The
language split is a milestone concern (Phase 5), not a label on every issue.


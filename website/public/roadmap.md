# Roadmap

This page separates SDK behavior that is **shipped today**, platform contracts
that are **verified live**, and capabilities that are **designed but not yet
shipped**.

  **How to read the badges.** Live means the
  platform-facing behavior is verified against a real Anypoint sandbox and
  usable now. Shipped means the SDK capability is
  implemented and available now; it does not imply that every platform-facing
  contract it uses is live-verified. Those caveats remain explicit on the
  capability page and in the [Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md).
  Phase 2 and friends mean *designed, with an
  agreed acceptance bar, but not yet shipped* — the API shapes on those pages
  are proposals and will move. Nothing here is described as working before it
  does.

## Why the SDK exists at all

The honest framing, because it determines everything below:

> A stock OpenAI client with a `base_url` and two headers can reach the
> governed proxy. The SDK is not selling you that.

What it sells is the **skeleton**: one place in your process where every
request enters and every response leaves. That is the only place where budget
headers, error classification, correlation IDs, cost tags, OTel spans,
simulation, and refusal handlers can all attach *without you wiring each one*.

The consequence, and the reason the roadmap is shaped the way it is: **the
skeleton is worth exactly the sum of what hangs on it.** Which is why the six
pieces below are one release and not spread across a year.

## Phase 1 — the six-piece minimum

The bar for Phase 1 is deliberately concrete: a developer who tries the SDK
for 15 minutes should find three things they cannot get from `base_url` plus
headers, and one of them should save them from a production incident.

Four core rejection shapes are live-verified; the remaining typed refusal paths
are shipped with explicit verification caveats. Budget and pacing, the local
simulator, testing and conformance, telemetry and cost, and the CLI and
decorators are shipped SDK capabilities; their pages call out any remaining
platform-verification caveats.

  
    A `403` from a PII policy is not an auth error, and a policy `429` must
    never be retried. Branch on governance outcomes instead of parsing bodies.
  
  
    Remaining token budget as a first-class object, so an overnight batch
    paces itself instead of dying at 2am.
  
  
    A local server that replays real rejection fixtures, so you can test your
    PII branch without a gateway.
  
  
    `simulate()` for in-process refusal injection, plus a pytest plugin you
    run against *your own* agent.
  
  
    OpenTelemetry GenAI spans, per-run correlation IDs, and validated
    cost-attribution tags.
  
  
    `donkey doctor` tells wrong credentials from wrong URL from
    model-not-allowed, instead of one opaque failure.
  

Alongside those: [Model access](https://donkey-development-kit.github.io/donkey-development-kit/frameworks.md) is live today, LangGraph becomes
the one deep, conformance-gated adapter, and the other seven frameworks are
supported at the `connection_kwargs()` level.

## Phase 2 — differentiate

  
    Discover governed MCP tools and bind them as native framework tools, with
    allow/deny filtering.
  
  
    `serve`, `expose`, and `dev` — make your agent callable by other agents,
    wrapping the official `a2a-sdk`.
  
  
    On-behalf-of token exchange, so per-user policy reaches the gateway and
    never silently falls back to the service identity.
  
  
    One vocabulary for pause-and-ask-a-human, mapped onto each framework's
    native interrupt.
  
  
    Derive a manifest and agent card from your code, then register them —
    from CI, not by hand.
  

Also in Phase 2: declarative refusal-reaction handlers, a classification
registry so custom gateway policies become typed exceptions, and kill-switch
awareness.

## Phase 3 — platform capabilities

  
    Read the in-force policy set and stop wasting calls that will be refused —
    advisory only, the gateway always wins.
  

Most of this phase is gated on the platform, not on effort: it needs a
policy-discovery endpoint that does not exist yet. Structured output on the
`.parse()` path and evaluation hooks are the parts that are not blocked.

## Phases 4 and 5

**Phase 4 — enterprise readiness.** Independent security review and
supply-chain hardening, a latency and overhead budget enforced in CI, a full
pass over every remediation string, the public API contract and deprecation
policy, and compliance evidence mapping.

**Phase 5 — complete rollout.** A TypeScript port of the six pieces, remaining
framework adapters brought back one at a time by demand, and 1.0 with
stability guarantees. The TypeScript gate is deliberate: Python
product-market fit first, because starting earlier means fixing every gateway
change twice.

## What this SDK will not build

Refusals, not backlog. At each of these boundaries the job is to make the
platform's own capability reachable and typed, not to reproduce it:

- Client-side policy enforcement — the gateway is the enforcement point.
- Client-side semantic caching.
- A provisioning control plane competing with API Manager or Terraform.
- Re-implementations of Agent Scanners, Kill Switch, or Trusted Agent Identity.
- An approval UI or queue.
- An evaluation framework.
- The gateway inside your agent process.
- A home-grown A2A protocol implementation — the official `a2a-sdk` is wrapped.

  **API shapes on planned Phase 2 and Phase 3 pages are proposals.** They exist
  so the design can be argued about concretely, and they will change before
  they ship. What is committed is the *behaviour* and the acceptance bar, not
  the signature.

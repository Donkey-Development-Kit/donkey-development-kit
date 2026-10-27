# ADR 0015: The SDK creates only the ingress instance in front of the developer's own agent or MCP server, never a control plane

- **Status:** Proposed
- **Date:** 2026-10-27
- **Issue:** #319. `donkey expose` (#226) implements the create side, gated
  by #254 (A2A) and #1278 (MCP); the read-only preflight is #1279.

## Context

The build plan's *Do not build, at any phase* list refuses "a provisioning
control plane competing with API Manager or Terraform". ADR 0008 (#730)
acted on that: the declarative `provisioning/` package (specs,
plan/diff/apply, drift, governance lint) and `governance.py` were deleted,
not parked.

BG §2.9.2 still specs `donkey expose`: provision an A2A proxy on Omni
Gateway, attach a policy set, register the card in Agent Registry. That is a
create path. The refusal and the feature were never reconciled, so two
verification issues each decided the question on their own: #88 (MCP Bridge
provisioning, since re-scoped to discovery) and #254 (the A2A proxy, "or
confirm there is no supported path"). Left implicit, the SDK grows a
provisioning surface one issue at a time, none crossing the line on its own,
and the sum is the control plane the plan refuses.

Two facts shape the line:

- **The calls exist.** `docs/verified-apis.md` §12.5 records the API Manager
  calls that create an ingress instance, attach inbound policies and deploy
  it to a gateway target (VERIFIED (plugin)). §7 and §12.3 record the
  Exchange publish and external-instance calls (VERIFIED (LIVE) for `mcp`
  assets). The connected-app write scopes are UNVERIFIED (§1).
- **The platform is adding its own IaC.** The roadmap's "LLM Gateway IaC"
  is first-class infrastructure-as-code for gateway configuration. An SDK
  that manages the shared infrastructure (gateways, targets, LLM proxies,
  org-wide policy) competes with it.

The developer's case for `expose` is narrow: they wrote one agent or one MCP
server, and they want the one ingress in front of it without a console
session. That is a single additive step on a resource they own, not a
desired-state model of the estate.

## Decision

1. **The SDK never holds desired state.** No spec file, no
   plan/diff/apply, no drift detection, no reconcile loop, and no state
   between runs other than what the platform returns. The refusal in the
   build plan means this, and ADR 0008's deletion stands. Enforced in
   review.
2. **The SDK may create exactly one thing per run: the ingress for the
   developer's own agent or MCP server.** That is one API Manager instance
   (an A2A agent or an MCP server endpoint on a gateway target that already
   exists), the inbound policies on that instance, and its Exchange or Agent
   Registry entry. This is `donkey expose` (BG §2.9.2). Enforced in review
   and by the command's tests.
3. **Creation is additive and idempotent.** An instance whose
   `instanceLabel` already exists in the environment is not created again.
   On an existing instance, the only change `expose` may make is to add a
   missing policy it was asked to add. It never deletes, never edits a
   policy's configuration, and never moves an instance to another target.
   Enforced by tests.
4. **Every create has a dry run, and every step reports its own outcome.**
   `--dry-run` prints what would be created and calls nothing that writes. A
   step that did not happen is never reported as done (BG §2.9.2, #226).
   Enforced by tests.
5. **Everything else is the platform's.** The SDK never creates, changes or
   deletes gateways, gateway targets, LLM proxies, org-wide or automated
   policies, connected apps, scopes, or environments. Where one of these is
   missing, the SDK reports it, names the resource, and points at the
   platform's IaC or console rather than dead-ending. A read-only preflight
   checks these. Enforced in review.
6. **Verification comes first (§0.3).** Every write call is a
   `docs/verified-apis.md` row at VERIFIED (LIVE) before it ships, and any
   call with no third-party support commitment (the `xapi` experience-API
   paths among them) is listed in the
   [unsupported boundary](../../website/content/reference/unsupported-boundary.mdx)
   page with its justification. Until then the command is
   `_verify.blocked(...)`. Enforced by the verified-apis gate in review.

### Alternatives considered

- **Never create anything; `expose` becomes a preflight plus a pointer to
  IaC.** Rejected: it drops the developer case BG §2.9.2 exists for. A
  developer who owns one agent should not need an IaC module to put a
  gateway in front of it. The preflight stays, for the resources rule 5
  keeps on the platform side.
- **Bring back a declarative spec for the agent's own resources only.**
  Rejected: desired state, however small, needs diff, drift and delete to
  mean anything, which is the deleted control plane again (ADR 0008).
- **Emit Terraform or IaC and let the platform apply it.** Rejected for now:
  `docs/verified-apis.md` records no format for the platform's IaC, so
  there is nothing verified to emit. Revisit if it ships, since it would let rule 2 delegate the
  write.
- **Shell out to the Agent Network CLI plugin.** Rejected: it is a
  Maven-project flow that needs a JVM (`docs/verified-apis.md` §5), and it
  provisions gateways, which rule 5 refuses.

## Consequences

- BG §2.9.2 covers MCP servers as well as A2A agents, and states rules 2–5.
  The build plan's *Do not build* entry and `ARCHITECTURE.md` point here.
- #226 is re-scoped to both instance types. #254 stays the A2A
  verification; MCP instance creation is #1278.
- The read-only preflight (#319's "SDK reads, verifies") is #1279, so this ADR can move to Accepted when `expose` ships.
- The connected app that runs `expose` needs API Manager write scope. That
  is UNVERIFIED today (#23) and is part of #254 and #1278.
- `expose` is the only write path to API Manager in the package. A second
  one needs a new ADR, so the line is argued once rather than per issue.
- TypeScript parity (Phase 5): the same rules apply. A port that adds an
  apply path reopens this decision.

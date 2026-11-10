# ADR 0026: Approval snapshots are isolated; missing native requesters fail closed

- **Status:** Accepted
- **Date:** 2026-11-09
- **Issue:** #1693 (private security remediation)
- **Supersedes:** ADR 0018 decision 4's requester-loss fallback only. Its
  per-client registry, routing, bounded native memory and other decisions stand.

## Context

The approval gate retained the caller's argument objects and exposed those same
nested objects through `Approval.arguments`. A shallow read-only mapping did not
prevent a caller or reviewer from changing a transfer after it was reviewed.
Async resolution added another delay between the decision and execution.

ADR 0018 allowed a native pause whose requester memory was lost to run under the
resumer's binding. A durable framework checkpoint preserves the pause, but not
evidence of the original user's identity or attribution. Neither an unbound
resumer nor a supplied user token establishes that lost evidence.

## Decision

1. The gate snapshots arguments before offering them for review. Execution and
   review use separate object graphs; `Approval` also detaches its arguments
   when constructed. The top-level review mapping is read-only. Nested review
   containers remain ordinary Python containers for framework serialization,
   but mutating them cannot change raise-and-resume execution arguments.
2. Snapshot only exact built-in scalar and container types, recursively. Preserve
   container types and Python defaults; an explicit MCP execution mode sends
   only supplied fields. A callable's `__signature__` never selects that mode.
   Refuse custom objects, container subclasses and cycles
   with actionable `ConfigError` before requesting approval. Applications convert
   these values to acyclic built-in data before calling a gated tool. This is not
   a general object-freezing guarantee: no custom copy, equality or serialization
   hook can prove an arbitrary mutable object safe. Shared container references
   may become independent copies; argument identity is not preserved.
3. A native approval must match both the private snapshot retained with its
   requester and the current bound argument snapshot. A reviewer mutation or
   mismatched call is refused rather than executed with different arguments,
   even if both the review object and the new call were changed together.
   Framework hooks receive a separate copy, not execution objects. Matching is
   recursive and type-aware, including mapping keys: `True`, `1` and `1.0`
    cannot substitute for each other, nor can different container types.
    Range parameters and floating-point binary representations, including signed
    zero and NaN payloads, must match exactly. Snapshot nesting is limited to 64
    containers, including the outer parameter mapping; deeper data is refused.
4. A native resume without retained requester evidence raises `AuthError`, records
   `donkey.approval.resume_refused`, and never executes the tool as the resumer.
   Recovery is a fresh approval in the original requester's context. Binding a
   token on a restored run is not a recovery mechanism for lost evidence.
5. Initial unbound native calls still pause normally. A retained service requester
   (`Requester` with no user) is valid state, distinct from a missing cache entry.
   Same-process restoration with retained requester state remains supported.
6. Only the initial native pause registers requester evidence. Repeated
   registration cannot overwrite it or extend its retention. Inspection,
    including OpenAI `pending_approvals()` on restored state, never registers
    origin evidence; the SDK's initial `needs_approval` callback does that.
    OpenAI snapshots its framework-validated Python arguments before the pause,
    so schema normalization is not confused with a later mutation. Inspection
    and replay retain the original correlation ID and timestamp; replayed
    callbacks must still match the retained exact validated snapshot.
7. ADK's trusted plugin explicitly supplies a one-call execution-only injection
   record. The gate verifies the injected object's identity, excludes it from
   review data, and executes with that same context. Arbitrary caller objects,
   parameter names and custom signatures receive no snapshot exemption.

## Alternatives Considered

- Shallow mapping proxies: do not isolate nested mutable objects.
- Unrestricted `deepcopy`: custom hooks can return the original object, retain
  shared mutable state or execute code. Copy success is not a freezing guarantee.
- Serialize everything to JSON: silently changes tuple/set/bytes semantics and
  cannot represent arbitrary Python values without unsafe custom hooks.
- Retain ADR 0018's warning-only fallback: changes the identity under which the
  approved action executes when evidence is unavailable.
- Persist requester credentials in framework checkpoints: introduces secret
  storage and trust-boundary requirements outside this remediation.

## Consequences

- Custom-object and cyclic arguments require conversion to built-in data. The
  change affects only approval-gated calls, not ordinary tool calls.
- Framework checkpoints remain durable, but a process restart or requester-cache
  eviction prevents execution of the old approval. Rejection and inspection of
  framework state do not themselves authorize execution.
- The approval remains advisory; this does not sandbox Python code or replace
  gateway policy enforcement. Framework model calls outside the gated tool keep
  the framework's resuming context, as before.
- Before release, synchronize the HITL and framework documentation, generated
  API reference, and published-artifact approval acceptance tests. This private
  remediation does not publish those changes.
- Regression tests in `test_approvals`, `test_approval_requester`,
  `test_langgraph_approvals`, `test_openai_agents_approvals` and
  `test_toolset_approval` pin isolation, refusal and retained-state restoration.

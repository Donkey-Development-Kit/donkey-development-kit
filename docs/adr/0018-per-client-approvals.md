# ADR 0018: Each Donkey owns its pending approvals; a gated call records on the client that runs it, else the one it was declared on

- **Status:** Accepted
- **Date:** 2026-10-30
- **Issue:** #1423 (part of #1414). The approval gate is from #211 (BG §2.3);
  the requester memory is from #1416.

## Context

`core/approvals.py` held one module-level `Approvals` instance, and
`Donkey.approvals` returned it, so every `Donkey` in a process shared one
registry of pending approvals (its docstring said so: "every `Donkey` returns
the same one"). Two clients in one process, such as two tenants' agents, two
proxies or a test beside an app, could each list, resolve or reject the
other's pending calls. A reviewer's handler built on client B could run a tool
that client A's run had asked about.

The registry was process-wide because `@Donkey.tool` is a `staticmethod`: the
decorator runs at import time, before any client exists, and the gate it
builds has no client to record on. `ToolSet.require_approval()` (#1153) built
its gates the same way, from a `ToolSet` that carries no client.

The framework pauses (LangGraph, the OpenAI Agents SDK, Strands, ADK) keep
their pause in the framework's own state, not in `donkey.approvals`. What DDK
keeps for them is the requester of each pause (#1416): the user and
attribution bound when the call paused, which the gate re-binds when the
approved call runs. That memory is process-wide too, keyed by the framework's
approval id, and limited to the latest 1,024 pauses.

The approval surface is new in 0.2.0: 0.1.3 has none of it, so no final
release's callers depend on the shared registry. The maintainer decided the
scope and the routing on #1423 (decisions of 2026-10-29).

## Decision

1. **Each `Donkey` owns one `Approvals`.** `Donkey.approvals` returns that
   client's registry, never another client's. `get`, `resolve` and `reject`
   of an id the client does not hold raise `ApprovalNotFound`.
2. **A gated call records on the first client that applies:**
   1. the client whose `donkey.run()`, `donkey.run_from_headers()` or
      `donkey.as_user()` block runs the call. `core/correlation.py` binds the
      client's registry in a context variable for the block, as it binds the
      run id;
   2. otherwise, the client the tool was declared on: `@donkey.tool(...)` read
      from an instance, or the client whose `tools.discover()` or
      `tools.resolve()` built the `ToolSet` that `require_approval()` gated;
   3. otherwise, the call raises `ConfigError` and the tool does not run. The
      remediation names both ways out.
3. **`Donkey.tool` stays a `staticmethod`.** `@Donkey.tool` on the class names
   no client and still works everywhere for an ungated tool. Read from an
   instance, the same function declares the tools it gates on that client.
   Its signature, and so the stable-surface snapshot, are unchanged.
4. **A framework pause's requester stays per process,** keyed by the
   framework's approval id, for the latest 1,024 pauses. An adapter's
   `pending_approvals()` records a pause with no client to hand, and the pause
   itself lives in the framework's state, which any client may resume. A
   resume with no recorded requester (another process, a restart, or past the
   latest 1,024) is not refused: it runs as the resuming code's binding and
   logs a `WARNING` on `donkey_kit.core.approvals`. A resume refused because
   it runs as another user records a `donkey.approval.resume_refused` span
   event before the `AuthError` leaves.
5. **The approval errors share a base.** `ApprovalError(DonkeyError)` is the
   base of `ApprovalRejected` (a reviewer refused a framework pause) and
   `GatewayApprovalDenied` (the user refused the gateway's step-up), which is
   also a `PolicyViolation`. They stay two types because different parties
   refused. `ApprovalNotFound` is a `DonkeyError` and a `KeyError`, for an
   unknown id and an already-decided one alike.
6. **`core/approval_types.py` is a core layer between `step_up` and `errors`.**
   It holds `Approval` and the approval errors, and imports only `errors`.
   `step_up` subclasses its `ApprovalError`, and `approvals` re-exports it.
   The "layers inside core" contract lists it, with no new ignored imports.
7. **`DescriptorMismatch` is a `DonkeyError` in the experimental tier.** It is
   also a `ValueError`, and is exported from `donkey_kit.experimental`, as the
   descriptor-derivation surface that raises it is.

Enforced by `tests/unit/test_approvals.py` (rules 1 to 3 and 5),
`tests/unit/test_toolset_approval.py` (rule 2 for discovered tools),
`tests/unit/test_approval_requester.py` (rule 4), the "layers inside core"
import-linter contract (rule 6), `tests/unit/test_error_taxonomy.py` and
`tests/unit/test_public_api_surface.py` (rules 5 and 7), and
`tests/unit/test_public_api_snapshot.py` (rule 3).

### Alternatives considered

- **Keep one registry per process and document it.** Rejected: one client can
  decide another's calls, which is the isolation a multi-client process needs.
- **Route only through the declaring client.** Rejected: `@Donkey.tool` at
  module level, the form most examples use, has no declaring client, and a
  tool shared by two clients would record on whichever declared it, not the
  one running it.
- **Route only through the running scope, and drop the declaring client.**
  Rejected: a gated tool called outside a `donkey.run()` block would always
  fail, including `@donkey.tool` on an instance, where the client is obvious.
- **Refuse a framework resume with no recorded requester.** Rejected for 0.2.0:
  a resume after a restart is the reason the framework pauses are durable, and
  refusing it would break that. The limit is documented, and the warning
  makes it visible.
- **Move the requester memory onto each client.** Rejected: an adapter's
  `pending_approvals()` and a framework's own resume run with no client to
  hand, and a framework pause can be resumed from any client.

## Consequences

- A gated tool declared with `@Donkey.tool(approval="required")` and called
  outside every client block raises `ConfigError` where it used to record on
  the shared registry. Tests and examples call gated tools inside
  `donkey.run()` or declare them on an instance.
- `ToolSet` carries the discovering client's registry. A `ToolSet` built by
  hand has none, so its gated calls need a client block.
- `donkey.approvals` docs (`hitl.mdx`, the API reference) describe the
  per-client scope and the routing; `errors.mdx` lists `ApprovalError`,
  `ApprovalNotFound` and `DescriptorMismatch`.
- A whole-server binding (ADK, Strands, OpenAI Agents, Agent Framework,
  CrewAI) of a set with a `require_approval()` rule raises `ConfigError` with a
  remediation, not `NotImplementedError("blocked on verification")`: there is
  no per-tool call to gate, which is a fact about the binding, not a missing
  verification.
- TypeScript parity, when it exists, must scope approvals per client and route
  them the same way.

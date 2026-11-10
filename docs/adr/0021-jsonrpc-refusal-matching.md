# ADR 0021: JSON-RPC refusals share the custom-policy registry through an explicit opt-in

- **Status:** Accepted
- **Date:** 2026-11-02
- **Issue:** #1385 (part of #1382; BG §2.2)

## Context

`core/tool_access.py` recognizes the captured MCP access-denial pair, while
`core/classifier.py` registers HTTP refusals. MCP and A2A JSON-RPC errors share
generic codes with ordinary protocol failures. The captured A2A ErrorInfo
reasons FORBIDDEN and INVALID_PARAMS do not identify PII or schema policies.
Adding the shared matcher changes the exhaustive core-layer contract (§1.1).

## Decision

1. `core/jsonrpc_refusals.py` is framework-free and walks mappings, duck-typed
   error objects, exception.error, causes, contexts and exception groups with
   cycle detection. Ordinary/ambiguous errors return None; explicitly
   policy-marked unknown errors return `PolicyViolation(policy="unknown")`.
2. `jsonrpc_code` explicitly opts a registration into JSON-RPC. It is an exact
   integer, never a bool or string. Status remains required: 200 or 4xx except
   401/429, matched exactly, never guessed when HTTP context is absent. A class
   policy slug and a policy-identifying body/header condition are required.
    Only exact nonempty identifiers in error.data.policy/error.data.reason or custom
    x-* headers establish identity. Protocol metadata, diagnostic strings and
    generic protocol/provider reasons are insufficient. Regexes may refine a match
    but never establish identity alone; standard security/transport x-* headers
    are excluded. The application owns its custom policy header contract.
3. The one process-wide registry retains its duplicate, specificity, tie,
   unregister and isolated_classify semantics. Registrations without jsonrpc_code
   remain HTTP-only. Classifier fills a hook in the shared matcher, as in ADR 0012,
   so the shared matcher never imports the registry above it.
4. `core.errors.classify` routes explicit JSON-RPC error envelopes through one
    installed hook; the public Classifier delegates to it. Verified HTTP built-ins
    precede RPC registrations, and 5xx remains UpstreamModelError. Unknown
   ordinary JSON-RPC failures become safe `ToolInvocationError`s. Normal HTTP
    rules and successful HTTP-200 transport behavior remain unchanged.
    Explicit envelopes require version 2.0, a string/integer/null id, and no
    result/error or tool-result contradictions. Invalid envelopes are not traversed.
    Duck envelopes obey the same metadata checks as mappings; plain exception.error
    wrappers remain valid. Auth challenges win at any RPC HTTP status except 429
    (rate precedence) and 5xx (outage precedence).
5. `_tool_access` holds the existing type and single-error rule below the matcher.
   `tool_access` re-exports the same public type/module identity and delegates
   its walk, returning only ToolNotInProfile. This built-in wins over custom
   registrations, including across nested exceptions.
6. Raw error.message/data never enter exception text or logs, except the existing
    denial tool-name extraction. No new framework binding, HTTP-200 interception,
    per-policy type, verification guard or evidence status changes here.
    HTTP built-ins receive only RPC discriminators, not diagnostic message/data
    or pattern labels; PII entities and gateway_message stay empty. The original
    response remains available only on .response, with no mutation of its bytes.

`test_jsonrpc_refusals.py`, existing tool-access/HTTP tests, architecture tests
and lint-imports enforce these rules. `_policy_sample` extracts the existing
regex sample generation to keep `_policy_spec` within its 500-line budget.

### Alternatives considered

- **Infer policy from code or diagnostics.** Rejected: shared codes and generic
  ErrorInfo reasons would misclassify ordinary server/argument errors.
- **Make status optional in registrations.** Rejected: changes the existing
  dataclass and specificity contract without a demonstrated need.
- **Lazy circular import from tool_access.** Rejected: the exhaustive layer
  contract counts lazy imports; a lower type/rule module avoids the cycle.
- **Separate JSON-RPC registry.** Rejected: duplicates lifecycle and tie handling.

## Consequences

MCP/A2A callers can explicitly invoke one matcher, using canonical envelope
body paths. Captured policy markers establish only a generic refusal here;
per-policy typing (#1386/#1387/#1394) and binding wiring (#1388) remain separate.
The existing HTTP transport does not inspect successful 200 responses for errors.
The helper is exported from donkey_kit and donkey_kit.core. Simulation supports
error-status RPC examples through the same dispatch, but rejects HTTP-200 examples
explicitly rather than promising refusal hooks that never run.

# ADR 0013: Binding refuses an MCP server that is not governed, with no opt-out

- **Status:** Superseded by [0017](0017-governed-discovery-default.md)
- **Date:** 2026-10-22
- **Issue:** #1150 (BG §2.7). Discovery itself is wired in #221.

## Context

`donkey.tools.discover()` (`donkey.py`) documents `governed=None` as its
default: discovery is unfiltered in v1, and flipping the default to `True` is
reserved for a later major version. So an unfiltered `ToolSet` can hold a
server that is registered in Exchange but not fronted by Omni Gateway, such as
a framework-built (FastMCP) server registered through #217 / #218 / #1105.

Binding such a server has two problems:

- **No governance.** Gateway policies, access profiles (#312) and the kill
  switch are not in the path.
- **Credential exposure.** The governed transport attaches the consumer's
  gateway credentials to every request. Binding would send them to a server
  the gateway does not front.

Today every `ToolSet` binding method (`tools/session.py`) is
`_verify.blocked` (`docs/verified-apis.md` §9), and so are discovery and
`registry.explain()`. No binding exists yet that could leak credentials, so the
rule can be fixed before #221 wires discovery. Which MCP transports the gateway
can front is not verified (`docs/verified-apis.md` §9), so the rule must not
depend on the transport.

## Decision

1. **Listing and binding are separate.** `discover(governed=None)` keeps its
   documented unfiltered default, and still lists an ungoverned server. Each
   `McpServerHandle` carries its `registry.explain()` verdict in
   `McpServerHandle.governance` (`None` when it was never evaluated).
2. **Binding refuses anything not positively governed.** Every `ToolSet`
   binding method first raises `UngovernedMcpServer` when any server in the set
   has `governance` of `None` or `governance.governed` false. The error names
   every refused server (`servers`) and every failed check (`reasons`, from
   `GovernanceReport.reasons_failed()`). Its remediation is to front the server
   with the gateway, or leave it out with `discover(governed=True)`. The check
   is on the verdict, never on the transport.
3. **There is no opt-out.** No `allow_ungoverned=True` on binding or discovery.
4. **The refusal comes first.** The guard runs before any connection opens or
   any credential is read, so a refused server receives no request.
5. `UngovernedMcpServer` lives in `donkey_kit.experimental` with the other
   errors of verification-blocked surfaces (ADR 0008), until binding is
   verified.

Enforced by `tests/unit/test_ungoverned_binding.py`. It covers each binding
method. It asserts the refusal, the reasons, that no httpx request is sent, and
that no opt-out parameter exists. The export rules are held by
`test_error_taxonomy.py` and `test_public_api_surface.py`.

### Alternatives considered

- **An `allow_ungoverned=True` opt-out that binds without gateway credentials.**
  Rejected: it gives an ungoverned path a supported SDK spelling, and the SDK
  adds nothing over the framework's own MCP client there, since no policy,
  telemetry or credential applies. Adding the flag later is not a breaking
  change, but removing it would be.
- **Make discovery governed-only by default.** Rejected: it breaks the
  documented `governed=None` contract, which is reserved for a major version.
  It would also hide ungoverned servers instead of explaining them.
- **Refuse only the ungoverned servers and bind the rest.** Rejected: the
  agent would silently get fewer tools than the set it asked for. The caller
  narrows the set explicitly instead, with `governed=True`.
- **Refuse by transport (for example, stdio).** Rejected: which transports the
  gateway can front is unverified (§0.3). The verdict already says whether the
  gateway fronts the server.

## Consequences

- `McpServerHandle` gains `governance: GovernanceReport | None = None`.
  Discovery (#221) must set it from `registry.explain()` on every handle it
  returns. A handle it leaves unset cannot be bound.
- `UngovernedMcpServer` is defined in `tools/session.py`, next to the guard
  that raises it, because `core/errors.py` is at its size budget
  (`test_architecture.py`). It is exported from `donkey_kit.experimental`, never
  from `donkey_kit`.
- The tool-access docs (`tool-access/binding`, `tool-access/discovery`) and
  the exception tree (`errors`) state the rule.
- A mixed set has to be narrowed before binding. `ToolSet.filter()` narrows
  tools, not servers, so it does not get past the guard.
- TypeScript parity, when it exists, must make the same refusal before any
  request.

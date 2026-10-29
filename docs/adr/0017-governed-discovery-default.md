# ADR 0017: Discovery is governed by default; binding still refuses an ungoverned server, with no opt-out

- **Status:** Accepted
- **Date:** 2026-10-29
- **Issue:** #1424 (part of #1414). The binding rule it carries over is from
  #1150 (BG §2.7).
- **Supersedes:** ADR 0013

## Context

ADR 0013 (#1150) made two choices that, together, set a trap. It kept
`donkey.tools.discover()` unfiltered by default (`governed=None`), because the
docstring reserved flipping the default for a later major version. And it made
every `ToolSet` binding refuse the whole set with `UngovernedMcpServer` when
any server in it is not governed (`_require_governed()` in `tools/session.py`).
So the shortest call, `await donkey.tools.discover()` followed by
`.langgraph()`, fails as soon as one ungoverned MCP asset exists anywhere in
the business group's Exchange, such as a framework-built server registered
through #217 / #218 / #1105 and never fronted by the gateway.

Discovery and binding have since been verified live (`docs/verified-apis.md`
§7, §9). No final release has shipped a working `discover()`: in 0.1.3 it
raises `_verify.blocked(...)`, and it first works in the 0.2.0 pre-releases
(#1308). So 0.2.0 is the last release where the default can change without
breaking a caller of a final release; after it, `ToolsFacade` is in the stable
surface (ADR 0006, #1425). The maintainer chose to flip the default there,
rather than keep it and document the trap (#1424, decision of 2026-10-29).

## Decision

1. **Discovery is governed by default.** `ToolsFacade.discover()` and
   `ToolsFacade.warm()` default to `governed=True`: the set holds only the
   servers that pass the default `GovernanceCriteria`, and each server left out
   is logged at info level with its failed checks. The two defaults are the
   same, so a default `warm()` fills the cache entry a default `discover()`
   reads.
2. **Unfiltered discovery is one argument away, and its meaning is unchanged.**
   `governed=None` (and `governed=False`) lists every MCP server, logs once
   that discovery is unfiltered, and attaches each server's `registry.explain()`
   verdict to `McpServerHandle.governance` (`None` when it was never
   evaluated). A `GovernanceCriteria` (such as `STRICT`) still sets the rules.
3. **Binding refuses anything not positively governed.** Unchanged from
   ADR 0013: every `ToolSet` binding method first raises `UngovernedMcpServer`
   when any server in the set has `governance` of `None` or
   `governance.governed` false. The error names every refused server
   (`servers`) and every failed check (`reasons`). Its remediation is to front
   the server with the gateway, or leave it out of the set (the default
   discovery does). The check is on the verdict, never on the transport.
4. **There is no opt-out.** Unchanged: no `allow_ungoverned=True` on binding or
   discovery.
5. **The refusal comes first.** Unchanged: the guard runs before any
   connection opens or any credential is read, so a refused server receives no
   request.

Enforced by `tests/unit/test_discover_default.py` (rules 1 to 3 and 5: a
mixed catalog through the public `donkey.tools.discover()`), by
`tests/unit/test_ungoverned_binding.py` (rules 3 to 5, for every binding
method) and `tests/unit/test_discover.py` (rule 2). The defaults are in the
stable-surface snapshot, `tests/unit/test_public_api_snapshot.py` (ADR 0006).

### Alternatives considered

- **Keep the unfiltered default and document the trap** (#1424 option C).
  Rejected: the shortest call is the one most code copies, and it would keep
  failing on a catalog with one ungoverned server. A docs note does not fix the
  default; it explains it.
- **Keep the unfiltered default, and have a binding built from an unfiltered
  set skip ungoverned servers with a warning** (#1424 option B). Rejected for
  the reason ADR 0013 gave: the agent would silently get fewer tools than the
  set it was handed. Narrowing stays explicit and happens at discovery, where
  each server left out is logged with its reasons.
- **Flip the default in a later major version, as the docstring said.**
  Rejected: every release that ships the unfiltered default adds callers that
  rely on it, and 0.2.0 is the first one that does, so it is the cheapest point.

## Consequences

- `await donkey.tools.discover()` returns a set that binds. An ungoverned
  server no longer appears in the default set, so code that listed the catalog
  with the default (to audit it, or to read `McpServerHandle.governance` of an
  ungoverned server) must pass `governed=None`. That breaks callers of the
  0.2.0 pre-releases; #1431 records it in `MIGRATION.md`.
- The `discover()` and `warm()` docstrings, the `UngovernedMcpServer`
  remediation, the unfiltered-discovery log line and the tool-access pages
  (`tool-access/discovery`, `tool-access/binding`) state the new default.
- ADR 0013's binding rules stand, restated here as rules 3 to 5. References to
  "ADR 0013" in `tools/session.py` and the docs still name where the guard
  came from.
- TypeScript parity, when it exists, must default to governed discovery and
  make the same refusal before any request.

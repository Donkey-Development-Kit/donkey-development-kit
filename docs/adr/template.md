# ADR NNNN: <the decision, stated as a sentence>

- **Status:** Proposed
- **Date:** YYYY-MM-DD
- **Issue:** #NNN (part of #NNN, if it belongs to an epic). Name the issue that
  implements the decision if it is a different one.
- **Supersedes:** (only if this replaces an earlier ADR: ADR NNNN)

<!--
Copy this file to docs/adr/NNNN-<slug>.md and add a row to the index in
docs/adr/README.md. See that README for when an ADR is required and what each
status means. Delete this comment.
-->

## Context

What problem forced a decision. State what the code and docs do today, with
file paths (`core/runtime.py`, `ARCHITECTURE.md`, "Layered architecture") and
issue numbers. Cite the spec where it applies (`BG §N.N`, a standing invariant
such as `§1.1`, or a `Phase N`). Keep it to the facts a reader needs to judge
the decision.

## Decision

What was decided, as numbered rules a reviewer can cite ("ADR NNNN, rule 2").
Use "is", "must" and "never". Say where the rule is enforced: a test, an
import-linter contract, a CI job, or review only.

### Alternatives considered

- **<Alternative>.** Rejected: <why>.

## Consequences

What changes because of the decision: code to move, docs to update, follow-up
issues, tests that now hold the rule, and anything that gets harder.

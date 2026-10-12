Closes #

<!-- Target develop. Cite the spec where the change implements or modifies
spec-governed behaviour (BG §N.N, a standing invariant such as §1.1, or a
Phase N). CONTRIBUTING.md §1, "The PR", has the full rules. -->

## Summary

-

## Test plan

<!-- Run from python/. CONTRIBUTING.md §1, "The pre-PR gate", lists the
sessions. -->

- [ ] `nox -s typecheck-and-lint test-3.11` (the quick gate)
- [ ] `nox` (every session: the whole blocking CI gate)
- [ ] `python scripts/verify_frameworks.py` (adapters or extras changed)

## ADR needed?

An ADR is required for a change to an `ARCHITECTURE.md` invariant, an
import-linter contract, the API stability tiers or the dependency policy. The
process is in `docs/adr/README.md`.

- [ ] No: this PR changes none of those.
- [ ] Yes: ADR `docs/adr/NNNN-<slug>.md` is added or updated in this PR, or
      already exists and is linked here.

## Post-deploy steps

None.

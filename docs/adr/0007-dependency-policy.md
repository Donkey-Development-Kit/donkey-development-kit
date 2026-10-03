# ADR 0007: Floors are the lowest verified versions, PR CI resolves from a lock, and the nightly run is the canary

- **Status:** Proposed
- **Date:** 2026-10-03
- **Issue:** #731 (part of #707). The lowest-direct floor job is #769; locking
  PR CI needs its own issue (see Consequences).

## Context

The build plan's standing invariant `§8.4` ("Extras are floors, never
ceilings") says `pyproject.toml` carries no upper pins, "a fresh resolve
always takes the newest release so the nightly matrix finds breakage early",
and known incompatibilities are documented in `docs/verified-apis.md`, not
encoded as pins. What the repo does today:

- **Floors.** Every extra in `python/pyproject.toml` declares only `>=`
  floors, and `tests/unit/test_house_style_config.py` rejects any specifier
  other than `>=` or `!=`. `docs/verified-apis.md` §8.3 records each floor as
  the lowest release verified for every kwarg its adapter passes (#743). Each
  row there was installed with `uv pip install --resolution lowest-direct` and
  passed `pytest tests/unit` and `verify_frameworks.py --only <fw>`, offline.
  No CI job keeps those floors honest yet (#769). Two jobs test one floor
  each: `adk-stacks` pins `google-adk==2.4.0` and `anthropic-stacks` pins
  `anthropic<1`, both in `.github/workflows/ci.yml`.
- **PR CI is not locked.** The repo has no Python lockfile or constraints
  file. Every job in `.github/workflows/ci.yml` runs `pip install -e
  ".[...]"`, so a PR's CI resolves the newest release of every dependency on
  the day it runs. An upstream release can turn a PR red that changed nothing
  related (the `[all]` resolution failure in #697 is that kind of break).
- **Nightly.** `.github/workflows/nightly-matrix.yml` runs at 06:00 UTC: the
  base gates, a `pip install --dry-run ".[all]"` on Python 3.10 to 3.12, and,
  for LangGraph only (the one conformance-tested adapter, `BG §1.8`), the
  signature check, the example smoke and `pytest`, all against the newest
  releases. The other seven framework extras aren't in it.

## Decision

1. **A floor is the lowest version verified to work.** Each floor in
   `python/pyproject.toml` is the lowest release where everything the SDK
   uses from that dependency behaves as `docs/verified-apis.md` records, and
   §8.3 there records why. Raising a floor needs that evidence; lowering one
   needs the same evidence at the lower version. A CI job installs each extra
   at its floors (`--resolution lowest-direct`) and runs its tests, so the
   floors stay true (#769).
2. **No ceilings.** Extras and base dependencies carry no upper bounds and no
   `==` pins (`§8.4`). A known-bad release is excluded with `!=` only when it
   breaks users and cannot be worked around, and §8.1 of
   `docs/verified-apis.md` records it.
3. **PR CI resolves from a committed lock.** The PR jobs install from a
   lockfile generated from `python/pyproject.toml`, so a PR is red only
   because of what it changed. The lock is refreshed by its own PR, on a
   schedule or when a dependency change needs it, and that PR runs the full
   gate. The jobs whose purpose is to test a resolve (`all-extra-resolves`)
   or a specific stack (`adk-stacks`, `anthropic-stacks`) keep resolving as
   they do now.
4. **The nightly run is the canary.** It resolves fresh against the newest
   releases, as `§8.4` intends, and a failure there is the signal that an
   upstream release broke something. It covers the base gates, the `[all]`
   resolve, and every conformance-tested adapter. A red nightly run leads to
   a fix, a recorded §8.1 incompatibility, or a lock refresh held back until
   one of those lands.
5. **Changing this policy needs an ADR** (see `docs/adr/README.md`).

### Alternatives considered

- **Keep PR CI on fresh resolves.** Rejected: it mixes two signals. A PR's
  gate should answer "did this change break something", and the nightly run
  already answers "did upstream break something". Fresh PR resolves also make
  a red build impossible to reproduce a day later.
- **Ceilings on the framework extras.** Rejected, as `§8.4` already decides:
  users would be held back from releases that work, and breakage would be
  found by users raising the ceiling instead of by the nightly run.
- **Floors set to whatever was current when the extra was added.** Rejected:
  a floor nobody tested can allow a release that silently drops a governed
  kwarg. `google-adk` below 2.4 dropped the governed client from `gemini()`
  until #844 raised the floor (#735), and #743 then audited every floor.

## Consequences

- Rule 3 is not implemented: there is no lockfile, and every PR job resolves
  fresh. It needs an issue for the lock format and tool, the jobs that move
  to it, and how the lock is refreshed. Until then this ADR stays proposed.
- Rule 1's CI job is #769. Until it lands, `docs/verified-apis.md` §8.3 is
  the record, as it says.
- The build plan's `§8.4` sentence that "a fresh resolve always takes the
  newest release" stays true for users and for the nightly run. This ADR
  narrows where CI does a fresh resolve; it doesn't change what users get.
- Adding a framework to the nightly matrix stays tied to promoting it to
  conformance-tested (`BG §1.8`, ADR 0004's roster-consistency test).
- `CONTRIBUTING.md` (the "Extras are floors" row of the §3 map) cites this ADR.

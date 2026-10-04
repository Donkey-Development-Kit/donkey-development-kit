# ADR 0006: Three API stability tiers, and a one-minor-release deprecation window during 0.x

- **Status:** Proposed
- **Date:** 2026-10-03
- **Issue:** #236 (the public API contract and deprecation policy; the
  architecture review's tiers proposal was folded into it rather than filed
  again, see #707). Recorded under the ADR process from #731.

## Context

- `docs/releasing.md` ("The public API surface semver governs") lists the
  versioned contract: the exception taxonomy and `classify()`, the `donkey.*`
  span attributes, the objects reachable from `donkey_kit` top level, and each
  adapter's `connection_kwargs()` shape. It excludes "the simulator, the
  conformance harness", although `simulate()`, `donkey mock` and the
  conformance pytest options are shipped features. It has no rule for breaking
  changes during 0.x.
- `donkey_kit.core` exports transport internals (`DonkeyAsyncClient`,
  `DonkeyClient` and their views, among others) with no stated stability.
- #719 defined the shape of the public surface: a sorted `__all__` per public
  module, every type in a public `Donkey` signature importable from
  `donkey_kit`, no private reach-ins across packages
  (`tests/unit/test_public_api_surface.py`, ruff `RUF022` and `SLF001`).
  Nothing records the surface itself, so an unannounced change to it passes CI.
- `CONTRIBUTING.md` §3 says a public symbol is deprecated with a
  `DeprecationWarning` before it is removed, and the existing deprecations do
  that (`Donkey.run_context()`, the `donkey_kit.registry.governance` alias).
  How long the warning must ship before the removal isn't written anywhere.
- `MIGRATION.md` describes the 0.1.1 changes as a clean break with no shims.

## Decision

1. **Three tiers.**
   - **Stable:** `donkey_kit.__all__`; the public methods and accessors of
     `Donkey`; the exception taxonomy and `classify()`; the `donkey.*` span
     attributes; the `donkey` CLI commands and flags; the conformance pytest
     options; the `KNOWN_LIMITATIONS` schema.
   - **Provisional:** `donkey_kit.core.*` and the keys inside each adapter's
     `connection_kwargs()`. They are public and documented, and they may change
     in a minor release with a migration note, without a deprecation cycle.
   - **Internal:** any name that starts with `_`, at any depth (`_on_request`,
     `_transport`, `donkey_kit._testing`), and any submodule path not listed
     above. Internal names change without notice.
2. **Breaking a stable name during 0.x** happens only in a minor release
   (`0.N` → `0.N+1`), and only after one earlier minor release shipped the old
   name as a working shim that emits a `DeprecationWarning`. A patch release
   never breaks a stable name. From 1.0, the same rule applies to major
   releases.
3. **One deprecation helper.** Shims emit their warning through a shared
   `_deprecated()` helper, so the message names the replacement and the
   release that removes the old name in one format.
4. **The stable surface is snapshotted.** A test compares `__all__` and the
   public signatures with a committed snapshot (or runs `griffe check` against
   the last release tag) and fails on a change the PR doesn't also make to the
   snapshot. Updating the snapshot is how a PR declares a surface change.
5. **The tiers live in `docs/api-stability.md`**, and `docs/releasing.md`
   points at it rather than keeping its own list.
6. **A change to the tiers or to this policy needs an ADR** (see
   `docs/adr/README.md`).

### Alternatives considered

- **No breaking changes during 0.x without a major bump.** Rejected: the
  package is alpha (`Development Status :: 3 - Alpha`) and still settling, and
  semver lets 0.x minors break. The window in rule 2 is the protection users
  get.
- **No deprecation window during 0.x (clean breaks, as in 0.1.1).** Rejected:
  a user who upgrades a minor release then has no release in which both the
  old and new names work.
- **Make `donkey_kit.core.*` stable.** Rejected for now: it still holds
  transport internals that #728 is about to move, and freezing them would
  block that work.

## Consequences

- `docs/api-stability.md`, the `_deprecated()` helper and the snapshot test
  are #236's to add, along with the migration guide it asks for.
- `docs/releasing.md` moves the simulator, `donkey mock` and the conformance
  pytest options into the contract, since they are shipped features.
- Removing blocked-only types from `__all__` (#730, ADR 0008) is a stable-tier
  change, so it follows rule 2 or moves the names somewhere that keeps them
  importable.
- New public names get reviewed against the tiers: a name added to
  `__all__` is a stable promise.

# ADR 0008: The supported CLI gets its own package, and the legacy provisioning code is quarantined

- **Status:** Accepted
- **Date:** 2026-10-03
- **Issue:** #730 (part of #707). Recorded under the ADR process from #731.

## Context

`ARCHITECTURE.md` ("Not in the stack — `provisioning/`") says the
`provisioning/` package holds two different things:

- **The refused declarative control plane**: specs, plan/diff/apply, drift,
  governance lint and publish. A provisioning control plane competing with API
  Manager or Terraform is on the build plan's "Do not build, at any phase"
  list. Its CLI commands are hidden and `_verify.blocked`, and the package is
  legacy scaffolding: "do not deepen it". `governance.py` at the package root
  is the same kind of scaffolding, with every verb blocked.
- **The supported `donkey` CLI.** The console script is
  `donkey = "donkey_kit.provisioning.cli:main"` (`python/pyproject.toml`
  `[project.scripts]`), and `provisioning/cli.py` hosts the live `init`,
  `test`, `mock` and `doctor` commands next to the hidden blocked stubs.
  `provisioning/doctor.py` is live code.

So the only supported CLI lives in the package the architecture freezes, and
it can't grow without deepening that package. Every wheel also ships the
refused code, and it shapes the base install:

- `pydantic>=2.6` is a base dependency, and its only importer in `src/` is
  `provisioning/spec.py` (ADR 0001).
- `donkey_kit.__all__` exports types that exist only for blocked surfaces:
  `AssetRef`, `GovernanceCriteria`, `GovernanceDrift`, `ProvisioningError`,
  `Publication`, `PublicationDrift`, `RegistryError`, `STRICT` and
  `ToolInvocationError` (checked against the current `__all__`).

## Decision

1. **The supported CLI moves to `donkey_kit/cli/`** (`init`, `doctor`, `mock`,
   `test`), and the console script becomes `donkey_kit.cli:main`. The package
   gets its own import-linter contract (with the contracts work in #707), so
   the CLI can't import the legacy code.
2. **The refused control plane leaves the shipped package.** `provisioning/`
   and `governance.py` are deleted, as the "Do not build" list implies, or
   parked under a `_legacy/` package that the wheel excludes. #730 picks one.
   The blocked verbs stay only as a hidden sub-app of the new CLI, and only if
   something still needs them.
3. **`donkey_kit.__all__` exports no type that exists only for a blocked or
   refused surface.** Such a type is removed or moved to a namespace that says
   what it is (for example `donkey_kit.experimental`). The removal follows the
   deprecation policy in ADR 0006.
4. **pydantic leaves the base dependencies.** Whatever validates an external
   schema after this imports pydantic lazily and declares it in an extra. A
   plain `pip install donkey-kit` installs no pydantic, and `import
   donkey_kit` works without it (tested). This implements the consequence ADR
   0001 deferred to #730.
5. **No new code goes into a quarantined package.** The CLI's growth happens in
   `donkey_kit/cli/`.
6. **`ExchangeRegistry` and `ToolsFacade` stay top-level.** They are in
   `donkey_kit.__all__` (`donkey_kit/__init__.py`) and reachable as
   `donkey.registry` and `donkey.tools`, because the surfaces they front are
   on the roadmap, not refused. Their method signatures take `AssetRef` and
   `GovernanceCriteria` (`registry/exchange.py`), which move to
   `donkey_kit.experimental`. So a top-level class has parameters whose types
   are importable only from the provisional namespace. That is intended: the
   methods still raise `NotImplementedError("blocked on verification: ...")`,
   and `from donkey_kit.experimental import AssetRef, GovernanceCriteria, STRICT`
   is how a caller builds the arguments. The old top-level names keep working
   through a module `__getattr__` that warns and forwards to `experimental`
   (ADR 0006 deprecation policy), not a silent drop.

### Alternatives considered

- **Leave the CLI in `provisioning/` and document it.** Rejected: the
  architecture says don't deepen that package, and every new CLI command would
  have to.
- **Keep the refused code in the wheel, hidden.** Rejected: it keeps a base
  dependency and public type names alive for surfaces the plan refuses, and
  users can't tell refused from roadmap.
- **Ship the CLI as a separate distribution.** Rejected: `donkey init`,
  `doctor`, `mock` and `test` use the SDK's own config, simulator and
  conformance code, and a second distribution would need its own release
  train for no user benefit.

## Consequences

- The whole-package import-linter `layers` contract from #729
  (`python/pyproject.toml`, `exhaustive = true`) orders the dev-only and
  legacy siblings above the production stack: `(cli)`, then
  `provisioning | governance`, then `conformance`, `simulator` and `_testing`,
  then `donkey`, `integrations`, `tools`, `registry`, `llm` and `core`. The
  legacy quarantine therefore sits above conformance, simulator and `_testing`:
  it may import them, and none of them may import it. #730 deletes
  `provisioning/` and `governance.py` (no `_legacy/` package is kept), so that
  layer entry has nothing left to hold once #730 lands and goes with them,
  leaving `cli` as the one sibling above the conformance harness. The
  "production code never imports the legacy packages" contract is replaced by
  #730's contract that no library module imports `donkey_kit.cli`.
- The `donkey` command is a stable surface (ADR 0006), so the commands and
  flags keep their names when they move. Only the import path of the module
  behind the console script changes.
- `CONTRIBUTING.md` §4 (the docs-sync map row for `provisioning/cli.py`),
  `ARCHITECTURE.md` ("Not in the stack — `provisioning/`") and
  `website/content/cli.mdx` follow the move.
- The acceptance repo checks the entry points, `__all__` and the extras, so
  #730 updates it in the same release.
- `pydantic.mypy` in `pyproject.toml` goes away with the last pydantic model,
  or stays scoped to it (ADR 0001).

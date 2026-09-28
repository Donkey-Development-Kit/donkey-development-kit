# Releasing `donkey-kit` to PyPI

Maintainer-facing reference for how a release becomes an installable package:
the **version & naming convention** it follows, and the **PyPI publish** wired as
two workflows, `.github/workflows/publish-pypi.yml` and
`.github/workflows/publish-testpypi.yml` (#206, #410, #674). The branch model and the
**promotion merge** that puts release content on `main` (squash to `develop`,
no-fast-forward merge to `main`) live in
[`CONTRIBUTING.md` §1](../CONTRIBUTING.md#1-branch-pr--release-workflow).

Everything below is self-contained — you should not need any other document to
cut a release.

## Versioning & naming

`donkey-kit` version strings are **PEP 440** (not raw SemVer), so they normalise
cleanly on PyPI. The version is **not invented per release** — it is the version
of the **milestone being shipped**. The milestone titles are the ground truth
(quote them verbatim, em-dash included):

| Milestone (exact title) | Ships version |
| --- | --- |
| `Phase 1 — Build the MVP (0.1.0)` | `0.1.0` |
| `Phase 2 — Differentiate, go beyond (0.2.0)` | `0.2.0` |
| `Phase 3 — Platform capabilities (0.3.0)` | `0.3.0` |
| `Phase 4 — Enterprise readiness (0.4.0)` | `0.4.0` |
| `Phase 5 — Complete rollout (1.0.0)` | `1.0.0` |

A milestone ships its **final** version only when it reaches **0 open issues**.
The `Verification` and `Upstream gaps` milestones have no version and never ship
— they are standing verification discipline gates, not releases.

### The pre-release ladder

Before a milestone is complete, promotions still land on `main` (docs, spec, and
scaffolding ahead of the feature work), and each gets a **pre-release** version
on the ladder toward the milestone's final version. The PEP 440 segments sort so
every pre-release is strictly less than the final, and PyPI orders them
correctly:

```
X.Y.Z.dev0  <  X.Y.Z.dev1  <  …  <  X.Y.Za1  <  X.Y.Zb1  <  X.Y.Zrc1  <  X.Y.Z
   dev0            devN            alpha 1       beta 1      rc 1        final
```

- **`.devN`** — dev snapshots toward the next version: work in progress on the
  way to the milestone's release, not yet promoted to a pre-release or final.
- **`aN` / `bN`** — alpha/beta: real feature surface exists, still unstable.
- **`rcN`** — release candidate: milestone all-but-complete, final validation.
- **final** (`X.Y.Z`) — the milestone hit 0 open issues and was promoted.

Spell pre-releases in the **normalised PEP 440 form** — `0.1.0a1`, never
`0.1.0-alpha.1` — so the git tag and the PyPI package version match.

### Tags

A release is an **annotated, `v`-prefixed git tag on `main`'s tip** (e.g.
`v0.1.0a1`), one per promotion, plus a GitHub Release. Tags are claims about
`main`, never `develop` or a feature branch. Everything below the milestone's
final version is flagged **pre-release** on its GitHub Release; only a final
`X.Y.Z` drops that flag.

### The version string lives in two files

The version is declared in **both** of these, which MUST always agree:

- `python/pyproject.toml` → `version`
- `python/src/donkey_kit/__init__.py` → `__version__`

Bump **both** on `develop`, in the PR that finishes a version's work, **before**
the promotion PR — so the code on `main` already reads the version its tag will
carry. A tag whose version disagrees with `__version__` at that commit is a bug.

## How a release reaches PyPI

Publishing uses **PyPI Trusted Publishing (OpenID Connect)** on both indexes —
there is **no long-lived API token** stored in the repo or in Actions secrets.
PyPI mints a short-lived token for the workflow run, keyed on the repository, the
workflow filename, and the GitHub Environment.

There are **two workflows, one per destination** (#410, #674), and they never
overlap:

- **Dev snapshots → TestPyPI, via `publish-testpypi.yml`'s manual
  `workflow_dispatch`.** Dev builds are deliberately **not** GitHub Releases —
  the Releases page is reserved for real releases. To dry-run: bump the
  `.devN` counter (see [Versioning & naming](#versioning--naming)) in **both**
  `python/pyproject.toml` and `python/src/donkey_kit/__init__.py` (they must
  agree), push to `develop`, then **Actions → Publish to TestPyPI → Run
  workflow** on `develop`. The publish job is guarded to
  `refs/heads/develop` — a dispatch from any other ref is skipped. It
  publishes to `https://test.pypi.org/legacy/` via OIDC — the same
  trusted-publishing path prod uses. Each dry-run needs a fresh `.devN`: a
  filename, once uploaded to TestPyPI, can never be reused, even after
  deletion.
- **Final release → production PyPI, via `publish-pypi.yml` reacting to a
  published GitHub Release.** The **first published GitHub Release is
  `0.1.0`**; that and every later final `X.Y.Z` route to prod. Finish the
  version's work on `develop`, promote `develop → main` (the no-fast-forward
  merge in
  [`CONTRIBUTING.md` §1](../CONTRIBUTING.md#1-branch-pr--release-workflow)),
  then tag `main`'s tip and cut a **non-pre-release** GitHub Release.
  Publishing it fires the prod path. A pre-release GitHub Release, if one is
  ever cut, publishes **nowhere** — `publish-pypi.yml`'s `publish-pypi` job
  still gates on `prerelease == false`, and `publish-testpypi.yml` has no
  `release:` trigger at all, so nothing picks it up. Prod stays protected
  rather than a mis-flagged pre-release being silently routed anywhere.

Each workflow runs its own `build` job first — sdist + wheel, `twine check`,
and the assertion that the built metadata carries only `>=` floors
(floors-never-ceilings) — then uploads via `pypa/gh-action-pypi-publish`.
Each is **structurally incapable** of reaching the other's destination:
`publish-pypi.yml` has no `workflow_dispatch` trigger, so a manual run can
never fire it; `publish-testpypi.yml` has no `release:` trigger, so a
published Release can never fire it. Neither workflow creates tags or
releases — they only react to them.

## The public API surface semver governs

The package follows semantic versioning (PEP 440 spelling). The versioned public
contract — the surface a **major** bump is reserved for breaking — is:

- The **exception taxonomy**: `DonkeyError` and its subclasses (`PolicyViolation`
  and descendants, `AuthError`, `TokenBudgetExceeded`, `UpstreamModelError`,
  `GatewayUnavailable`, …), their inheritance relationships, and the
  `classify()` mapping from a rejection shape to a type.
- The **OpenTelemetry `donkey.*` span attributes** emitted by the telemetry
  layer (the `gen_ai.*` keys are transcribed literals pinned to
  `GEN_AI_SEMCONV_VERSION`; changing that pin is a deliberate, called-out change,
  not a transitive-dependency surprise).
- The public objects reachable from `donkey_kit` top level (`Donkey`,
  `DonkeyConfig`, the `donkey.*` accessors) and each adapter's
  `connection_kwargs()` shape.

Internal modules (`core/_verify.py`, transport internals, the simulator, the
conformance harness) are **not** part of the contract.

There is intentionally **no `CHANGELOG.md`**. The **GitHub Release is the
changelog** (`[project.urls].Changelog` points at the Releases feed); release
notes call out breaking changes, verification-status flips, and extras
changes (the floors-never-ceilings rule). For a pre-release, the notes also state plainly what is *not* yet real
(e.g. "pre-MVP: docs and scaffolding only") so a `.devN` build never reads like a
usable SDK.

## One-time human setup: register the Trusted Publisher

Trust is registered **per index, and keyed on the workflow filename** — each
index must point at the filename that actually publishes to it, or PyPI
rejects the OIDC token. This is a manual step on the web UI (it cannot be
done from CI), performed **once per index**. `donkey-kit` already exists on
both PyPI and TestPyPI, so edit each project's **existing** trusted publisher
(the project's Settings → Publishing) to point at the workflow filename in the
table below. The **pending publisher** form (account → Publishing) is only the
fallback for an index where the project does not exist yet. Enter, on the
**GitHub** tab:

| Field | On **pypi.org** | On **test.pypi.org** |
| --- | --- | --- |
| PyPI Project Name | `donkey-kit` | `donkey-kit` |
| Owner | `Donkey-Development-Kit` | `Donkey-Development-Kit` |
| Repository name | `donkey-development-kit` | `donkey-development-kit` |
| Workflow name | `publish-pypi.yml` | `publish-testpypi.yml` |
| Environment name | `pypi` | `testpypi` |

These must match the workflow exactly or PyPI rejects the OIDC token.

Then, in the GitHub repo (Settings → Environments), create the `pypi` and
`testpypi` **Environments**. Adding **required reviewers** to `pypi` is
recommended: the publish job then pauses for a human approval before the
irreversible production upload — the go-ahead gate #339 requires.

> A public PyPI publish is effectively irreversible (names can be squatted;
> releases can only be *yanked*, never deleted). Do not change the
> **production** publisher or approve a `pypi` deployment until the release is
> genuinely ready — #339 was the first-publish checklist (completed 2026-09-25).

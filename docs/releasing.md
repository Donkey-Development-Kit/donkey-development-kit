# Releasing `donkey-kit` to PyPI

Maintainer-facing reference for how a release becomes an installable package.
The **version scheme, tagging, and GitHub Release notes** are owned by the
`ddk-release` skill; the **promotion merge** that puts release content on `main`
is owned by `ddk-merge-strategy`. This page covers the piece those two defer:
the **PyPI publish**, wired as `.github/workflows/release.yml` (#206).

## How a release reaches PyPI

Publishing uses **PyPI Trusted Publishing (OpenID Connect)** on both indexes —
there is **no long-lived API token** stored in the repo or in Actions secrets.
PyPI mints a short-lived token for the workflow run, keyed on the repository, the
workflow filename, and the GitHub Environment.

There are **two paths, one per destination** (#410), and they never overlap:

- **Dev snapshots → TestPyPI, via manual `workflow_dispatch`.** Dev builds are
  deliberately **not** GitHub Releases — the Releases page is reserved for real
  releases. To dry-run: bump the `.devN` counter (the `.devN → aN → bN → rcN`
  ladder, `ddk-release`) in **both** `python/pyproject.toml` and
  `python/src/donkey_kit/__init__.py` (they must agree), push, then
  **Actions → Release → Run workflow** on that ref. It publishes to
  `https://test.pypi.org/legacy/` via OIDC — the same trusted-publishing path
  prod uses. Each dry-run needs a fresh `.devN`: a filename, once uploaded to
  TestPyPI, can never be reused, even after deletion.
- **Final release → production PyPI, via a published GitHub Release.** The
  **first published GitHub Release is `0.1.0`**; that and every later final
  `X.Y.Z` route to prod. Finish the version's work on `develop`, promote
  `develop → main` (`ddk-merge-strategy`), then tag `main`'s tip and cut a
  **non-pre-release** GitHub Release (`ddk-release`). Publishing it fires the
  prod path.

Either trigger runs the same `build` job first — sdist + wheel, `twine check`,
and the assertion that the built metadata carries only `>=` floors
(floors-never-ceilings, §8.4) — then uploads via `pypa/gh-action-pypi-publish`.
A manual dispatch is **structurally incapable** of reaching prod: the
`publish-pypi` job gates on `github.event_name == 'release'`, so only a published
Release can trigger it. The workflow never creates tags or releases — it only
reacts to them.

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
notes call out breaking changes, §0.3 verification-status flips, and §8.4 extras
changes, per `ddk-release`.

## One-time human setup: register the Trusted Publisher

The workflow is inert until the trust is registered on PyPI's side. This is a
manual step on the web UI (it cannot be done from CI), performed **once per
index**. Because `donkey-kit` is not yet published, use the **pending publisher**
form (Your projects → Publishing, or account → Publishing before the project
exists). Enter, on the **GitHub** tab:

| Field | Value |
| --- | --- |
| PyPI Project Name | `donkey-kit` |
| Owner | `Donkey-Development-Kit` |
| Repository name | `donkey-development-kit` |
| Workflow name | `release.yml` |
| Environment name | `pypi` (production) / `testpypi` (test.pypi.org) |

These must match the workflow exactly or PyPI rejects the OIDC token. Do the
same on **test.pypi.org** with Environment `testpypi` for the pre-release path.

Then, in the GitHub repo (Settings → Environments), create the `pypi` and
`testpypi` **Environments**. Adding **required reviewers** to `pypi` is
recommended: the publish job then pauses for a human approval before the
irreversible production upload — the go-ahead gate #339 requires.

> A public PyPI publish is effectively irreversible (names can be squatted;
> releases can only be *yanked*, never deleted). Do not submit the **production**
> pending publisher or approve a `pypi` deployment until the release is genuinely
> ready — see #339 for the first-publish checklist.

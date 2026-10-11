# Python support policy

`donkey-kit` supports the CPython versions that are still maintained upstream
and that CI tests. This page says which versions those are and when a version
is added or dropped (#745).

<!-- toc -->
**Contents**

- [Supported versions](#supported-versions)
- [The rules](#the-rules)
- [Python 3.10](#python-310)
- [Raising the floor: the checklist](#raising-the-floor-the-checklist)
<!-- tocstop -->

## Supported versions

| Python | Supported | CPython end of life |
| --- | --- | --- |
| 3.10 | Yes, until the first minor release after its end of life | October 2026 |
| 3.11 | Yes | October 2027 |
| 3.12 | Yes | October 2028 |
| 3.13, 3.14 | Not yet tested | October 2029, October 2030 |

The end-of-life dates are the ones in the CPython
[status page](https://devguide.python.org/versions/).

## The rules

1. **A supported version is a tested version.** Every supported version has a
   leg in the CI `test` matrix (`.github/workflows/ci.yml`) and a
   `Programming Language :: Python :: 3.x` classifier in
   `python/pyproject.toml`. The two lists match, and
   `tests/unit/test_house_style_config.py` fails when they drift.
2. **The floor follows the CPython end-of-life schedule.** When a version
   reaches end of life upstream, `donkey-kit` drops it in its **first minor
   release** after that date (for example `0.1.x` → `0.2.0`). A patch release
   never raises `requires-python`.
3. **A drop is announced.** The release that drops a version says so in
   `MIGRATION.md` and in its release notes' Breaking changes section. The
   minor line before it keeps working on the dropped version and gets security
   fixes as described in [`SECURITY.md`](../SECURITY.md).
4. **A new version is added once CI passes on it.** Adding a version means
   adding its matrix leg and its classifier in the same PR.

## Python 3.10

Python 3.10 reaches end of life in October 2026. The `0.1.x` line keeps
supporting it. The first minor release after that date raises
`requires-python` to `>=3.11`, removes the 3.10 matrix leg and classifier, and
drops the `tomli` backfill, which only 3.10 needs.

## Raising the floor: the checklist

When a version is dropped, these move together in one PR:

- `requires-python` in `python/pyproject.toml`;
- the version classifiers in `python/pyproject.toml`;
- the `test` matrix in `.github/workflows/ci.yml` and
  `.github/workflows/nightly-matrix.yml`;
- ruff `target-version` and mypy `python_version`;
- backfills only the dropped version needed (`tomli`, `typing-extensions`)
  and their `python/dependency_allowlist.toml` entries;
- the table on this page, `CONTRIBUTING.md`'s floor rule, and `MIGRATION.md`.

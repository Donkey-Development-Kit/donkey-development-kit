# `docs/` — engineering reference

Maintainer-facing reference docs for building the SDK. These are **not** the
consumer docs: for *using* the SDK see the Nextra site under `website/`; for
*working in* the repo (branch/PR flow, testing, conventions) see
[`../CONTRIBUTING.md`](../CONTRIBUTING.md); for the architecture map see
[`../ARCHITECTURE.md`](../ARCHITECTURE.md); the authoritative specs are in
[`../spec/`](../spec/) — the build plan (phases, invariants) and the build
guide (feature scope, cited `BG §N.N`).

Everything here serves the **verification discipline**: *never invent an
endpoint, header name, or class name.* The files track what has been proven
against a real Anypoint sandbox versus what is still assumed or blocked.

| File | What it is |
| --- | --- |
| [`verified-apis.md`](verified-apis.md) | **The verification ledger** — the single source of truth for every endpoint, header, and class name the SDK touches and its status (`VERIFIED (LIVE)` / `VERIFIED (CLI)` / `VERIFIED (plugin)` / `UNVERIFIED` / blocked). When a value is confirmed against a sandbox, flip its row here **and** replace its `Unverified(...)` placeholder in `core/_verify.py` with a plain constant — the two edits move together. |
| [`unsupported-boundary.md`](unsupported-boundary.md) | **The procurement doc** — classifies every platform API the SDK calls (public / no-SLA / undocumented), so enterprise-buyer questions get a five-minute answer instead of a two-week stall. Its "Undocumented surfaces" section is designed to stay empty. |
| [`python-support.md`](python-support.md) | **The Python support policy** — which CPython versions are supported and tested, and when one is dropped (the first minor release after its end of life). |
| [`adr/`](adr/README.md) | **Architecture decision records** — one file per design decision (context, decision, alternatives, consequences, status), with the template and the index in [`adr/README.md`](adr/README.md). An ADR is required for any change to an `ARCHITECTURE.md` invariant, an import-linter contract, the API stability tiers or the dependency policy (#731). |
| [`releasing.md`](releasing.md) | **How a release reaches PyPI** — the Trusted Publishing (OIDC) workflows wired as `.github/workflows/publish-pypi.yml` and `.github/workflows/publish-testpypi.yml` (#206, #674), the public API surface semver governs, and the one-time human step to register the trusted publisher. The version scheme and tag convention are in its *Versioning & naming* section. |

*"Agent Fabric", "Anypoint", and "Omni Gateway" are Salesforce trademarks; this
project is a descriptive, non-first-party SDK for consuming those capabilities
(the trademark/support boundary).*

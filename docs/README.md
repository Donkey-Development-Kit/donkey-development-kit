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
| [`verified-apis.md`](verified-apis.md) | **The verification ledger** — the single source of truth for every endpoint, header, and class name the SDK touches and its status (`VERIFIED (LIVE)` / `VERIFIED (CLI)` / `VERIFIED (plugin)` / `UNVERIFIED` / blocked). When a value is confirmed against a sandbox, flip its row here **and** set `verified=True` in `core/_verify.py` — the two edits move together. |
| [`unsupported-boundary.md`](unsupported-boundary.md) | **The procurement doc** — classifies every platform API the SDK calls (public / no-SLA / undocumented), so enterprise-buyer questions get a five-minute answer instead of a two-week stall. Its "Undocumented surfaces" section is designed to stay empty. |
| [`releasing.md`](releasing.md) | **How a release reaches PyPI and is observed afterward** — the Trusted Publishing (OIDC) workflow wired as `.github/workflows/release.yml` (#206), the private adoption archive/dashboard (#515), the public API surface semver governs, and the one-time human step to register the trusted publisher. The version scheme and tagging live in the `ddk-release` skill; this is the publish half. |

*"Agent Fabric", "Anypoint", and "Omni Gateway" are Salesforce trademarks; this
project is a descriptive, non-first-party SDK for consuming those capabilities
(the trademark/support boundary).*

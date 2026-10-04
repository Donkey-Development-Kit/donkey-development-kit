# Architecture decision records

An architecture decision record (ADR) is a short file that records one design
decision: the problem, what was decided, what was rejected, and what follows
from it. [`ARCHITECTURE.md`](../../ARCHITECTURE.md) states the rules the code
follows today. The ADRs say why each rule was chosen and what was considered
instead, so a later change can argue with the reasons and not only with the
rule.

The process was introduced in #731 (part of the architecture epic #707).

## When an ADR is required

A pull request needs an ADR (a new one, or a new one that supersedes an older
one) when it changes any of these:

1. **An `ARCHITECTURE.md` invariant.** A rule that document states as holding
   for the whole package: the layered stack and the framework-free core, the
   transport as the single attachment point, the error-taxonomy invariants, the
   verification discipline, the three ergonomic forms per adapter, and so on.
   Correcting a description so it matches what the code already does is not a
   change to the invariant and needs no ADR.
2. **An import-linter contract.** Adding, removing or loosening a
   `[[tool.importlinter.contracts]]` entry in
   [`python/pyproject.toml`](../../python/pyproject.toml), including its
   `ignore_imports` exceptions.
3. **The API stability tiers.** Which names are stable, provisional or
   internal, and the deprecation policy that governs them
   ([ADR 0006](0006-api-tiers-and-deprecation.md)).
4. **The dependency policy.** How floors are chosen, whether ceilings are
   allowed, and what CI resolves against
   ([ADR 0007](0007-dependency-policy.md)).

Anything else may have an ADR when the reasoning is worth keeping, but doesn't
have to. The PR template asks the question on every PR
([`.github/pull_request_template.md`](../../.github/pull_request_template.md)).
A reviewer can ask for an ADR on any PR whose change matches the list above.

## How to write one

1. Copy [`template.md`](template.md) to `docs/adr/NNNN-<slug>.md`. `NNNN` is the
   next unused number, zero-padded to four digits; `<slug>` is a few
   kebab-case words. Numbers are never reused, even when an ADR is rejected.
2. Fill in the context, the decision, the alternatives you rejected, and the
   consequences. Cite files and issues (`core/runtime.py`, `#725`) and the spec
   the way the rest of the repo does (`BG §N.N`, a standing invariant such as
   `§8.4`, or a `Phase N`). Don't state anything about a platform API that
   [`docs/verified-apis.md`](../verified-apis.md) doesn't back up (§0.3).
3. Add a row to the index below in the same PR.
4. Open the PR with the change the ADR describes, or on its own if the
   decision comes first.

## Status

| Status | Meaning |
| --- | --- |
| **Proposed** | The decision is agreed as the direction, and the code may not do it yet. The ADR names the issue that implements it. |
| **Accepted** | The decision is in effect: the code does what the ADR says. |
| **Superseded by NNNN** | A later ADR replaced this one. The old file stays, with its status changed and a link to the new one. |
| **Rejected** | The decision was considered and not taken. The file stays, so the reasoning isn't lost. |

A proposed ADR becomes accepted in the PR that makes the code match it: that
PR changes the status line and the index row. Once an ADR is accepted, its
decision doesn't change. To change it, write a new ADR that supersedes it.
Fixing a typo, a broken link, or a file path that moved is fine at any time.
When a later ADR makes a *consequence* of an accepted ADR untrue but leaves its
decision standing, the older file gets an "Amended by" note under its status
line and a dated "Amendment" section after its consequences; the decision text
is not rewritten.

## Index

| ADR | Title | Status | Issue |
| --- | --- | --- | --- |
| [0001](0001-value-objects.md) | Value objects are frozen dataclasses; pydantic only at external-schema boundaries | Accepted | #723 |
| [0002](0002-typed-refusal-bridge.md) | One framework-agnostic typed-refusal bridge | Accepted | #724 |
| [0003](0003-core-runtime.md) | A core `Runtime` with a process default | Accepted | #725 |
| [0004](0004-adapter-protocol-and-roster.md) | The adapter contract and a single roster | Accepted | #726 |
| [0005](0005-config-precedence.md) | Config precedence: kwargs, env, local file, project file, user file, default | Accepted | #727 |
| [0006](0006-api-tiers-and-deprecation.md) | API stability tiers and the 0.x deprecation policy | Proposed | #236 |
| [0007](0007-dependency-policy.md) | Dependency policy: verified floors, locked PR CI, the nightly run as the canary | Proposed | #731 |
| [0008](0008-legacy-quarantine-and-cli-home.md) | Quarantine the legacy provisioning code and give the CLI its own package | Accepted | #730 |
| [0009](0009-sans-io-transport.md) | A sans-IO transport policy, and no default retry of model POSTs on 502/504 | Accepted | #728 |
| [0010](0010-no-hidden-global-side-effects.md) | No hidden global side effects: the OTel provider | Accepted | #732 |

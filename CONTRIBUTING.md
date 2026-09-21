# Contributing

Thanks for working on the Donkey Development Kit. This guide is the contributor-facing
runbook for the repo: how a change moves from an issue to `main`, how the code is
tested and linted, and the conventions that keep the package trustworthy. It
serves both **internal** contributors (org members with push access) and
**external** ones (contributing via a fork + PR); where the two diverge is
spelled out in [§1](#who-can-contribute-internal-vs-external).

For *how the SDK is built* — the layer boundaries, the verification discipline,
the error taxonomy, adapter support depth — read [`ARCHITECTURE.md`](ARCHITECTURE.md)
first; this guide assumes it. The authoritative specs behind both live in
[`spec/`](spec/): the build plan owns phases and invariants, the build guide
owns feature scope (cited `BG §N.N`), and a bare `§N.N` resolves into the
archived v1 plan. When a rule here feels arbitrary, read the cited section.

> **These conventions are also encoded as Claude Code skills** under
> `.claude/skills/ddk-*/` (one runbook per topic, listed in
> [`.claude/skills/README.md`](.claude/skills/README.md)). Those skills are the
> machine-readable, operational form for agent-assisted work; this document is
> their human-readable distillation. If the two ever diverge, the skills are the
> source of truth for the exact `gh` commands, and this file is the narrative — a
> discrepancy is a bug worth reporting.

All Python work happens in `python/`; commands below are run from there unless
noted. There is no Makefile — every command runs directly.

---

## 1. Branch, PR & release workflow

### Who can contribute: internal vs external

This repo is **public** and takes contributions two ways. There is only one
workflow; what differs is *where your branch lives and who can triage and merge
it* — not the discipline.

- **Internal contributors** — members of the `Donkey-Development-Kit` org with push
  access. You branch directly in this repo, push your `<type>/<issue#>-<slug>`
  branch to `origin`, set your own milestone/labels/assignee, and (after review)
  merge. The rest of this document is written from this seat.
- **External contributors** — anyone without push access. You work from a
  **fork** and open a PR from your fork into this repo's `develop`. Everything
  substantive is identical — issue-first, the same branch naming, the same
  pre-PR gate, the same docs-sync rule — with the exceptions called out as
  **(fork)** notes throughout this section.

**The fork flow, end to end.** Fork `Donkey-Development-Kit/donkey-development-kit` on
GitHub, then clone your fork and add this repo as `upstream`:

```bash
git clone https://github.com/<you>/donkey-development-kit.git
cd donkey-development-kit
git remote add upstream https://github.com/Donkey-Development-Kit/donkey-development-kit.git
```

Branch from `upstream/develop` (not your fork's possibly-stale copy), keep the
`<type>/<issue#>-<slug>` name, and push the branch to your fork (`origin`):

```bash
git fetch upstream
git checkout -b docs/13-verified-apis-update upstream/develop
git push -u origin docs/13-verified-apis-update
```

Open the PR from `<you>:docs/13-verified-apis-update` →
`Donkey-Development-Kit:develop`, and **tick "Allow edits by maintainers"** so a
maintainer can rebase or push a small fix without a round-trip. Keep your branch
current by rebasing on `upstream/develop` (`git fetch upstream && git rebase
upstream/develop`), the same default as an internal branch.

What a fork contributor **can't** do — a maintainer does each instead, so don't
block waiting on it:

- **Triage fields.** You can file an issue, but setting its milestone, labels,
  and assignee needs write access. File it, then say in the issue that you plan
  to work it, so it isn't picked up by someone else.
- **The `.claude/skills/**` shortcut** (below) — committing straight to
  `develop` — needs push access. From a fork, even a skills-only change goes
  through a PR.
- **Merging and promotion** — squashing a branch into `develop` and promoting
  `develop` → `main` are maintainer-only.
- **Secret-gated CI and live/sandbox verification** — see
  [the pre-PR gate](#the-pre-pr-gate) and [§2](#2-testing-strategy).

### The issue is the plan

**No code change lands without a GitHub issue and a branch named after it.**
Small changes are exactly where this discipline gets skipped and history gets
unanchored, so there is no "too small for an issue" exception. Plan content
lives in the issue (edit the body or comment) — not in committed `plans/*.md`
scratch files.

The one exception: edits scoped **entirely** to `.claude/skills/**` may be
committed directly to `develop` after a recap, since skills are agent tooling
rather than SDK code. Anything touching `python/`, `website/`, CI, the build
plan, or repo policy follows the full flow below. **(fork)** This shortcut needs
push access — from a fork, a skills-only change still goes through a PR.

### Branch model

Two long-lived branches, each with a different job:

- **`develop`** — the integration branch. Its log reads as an **issue log**: one
  commit per closed issue/PR. All feature/fix branches target it.
- **`main`** — the release branch. Its log reads as a **release log**: one merge
  commit per promotion. It moves only when `develop` is promoted.

**Always branch from `develop`; never branch from or PR into `main`.** If you
find yourself on `main` about to start work, `git checkout develop` first.

### The lifecycle

1. **Find or file the issue.** Search first (`gh issue list --repo
   Donkey-Development-Kit/donkey-development-kit --search "<keywords>"`); file one if none
   matches. Every issue carries exactly one **milestone** — that milestone is the
   release the branch targets. Triage is by milestone + labels; there is no
   Projects board. **(fork)** File the issue, but leave milestone/labels/assignee
   to a maintainer — setting them needs write access; note in the issue that you
   plan to work it.
2. **Cut the branch from `develop`:**
   ```bash
   git fetch origin
   git checkout develop && git pull --ff-only
   git checkout -b <type>/<issue#>-<slug>
   ```
   Branch name format is `<type>/<issue#>-<slug>` — the issue number is
   mandatory. `<type>` is one of `feat` (new capability), `fix` (something that
   should have worked), `docs` (README, website, comments), or `chore`
   (tooling, deps, refactors). The slug is 2–5 kebab-case words describing *what*
   changes, e.g. `fix/42-proxy-url-trailing-slash`. **(fork)** Branch from
   `upstream/develop` and push to your fork (`origin`) instead — see the fork
   flow above.
3. **Re-confirm the boundaries before writing code** (see
   [`ARCHITECTURE.md`](ARCHITECTURE.md)): the change stays within the layering
   (`integrations → tools → registry → llm → core`, lower never imports higher);
   `core/` stays framework-free; and any endpoint/header/class name it depends on
   is already `VERIFIED` in [`docs/verified-apis.md`](docs/verified-apis.md) — if
   not, that is a verification question (`_verify.blocked(...)` or an
   `Unverified(...)` placeholder), not a place to guess (verification discipline).
4. **Commit, push, open a PR into `develop`.** Once you're on a correctly-named
   branch, commit and push autonomously — the branch is the isolation boundary.
   Commit messages cite `§N.N` when the change implements or modifies
   spec-governed behavior, e.g. `fix(llm): correct proxy base URL handling
   (config resolution)`.

**Use a worktree when there's any chance of a parallel session** (another editor
window, a running dev server, a `pytest --looponfail` holding files): one issue =
one branch = one worktree. A fresh worktree has no installed venv/extras — run
`pip install -e ".[dev,llm,cli]"` in its `python/` before testing.

**No workarounds for prerequisites.** If work on issue #N turns out to need an
out-of-scope change first (a missing `core/` primitive, a verification unblock),
**stop and surface it** — file a linked issue rather than silently expanding #N's
scope or guessing at an unverified value.

### The pre-PR gate

Before drafting the PR, run the exact checks CI runs, from `python/`. Any
non-zero exit means stop and fix before opening the PR:

```bash
cd python
pytest -q          # the `test` matrix job (3.10/3.11/3.12 in CI)
mypy               # mypy --strict, BLOCKING
ruff check .       # E,F,I,UP,B; line-length 100
lint-imports       # the layered, framework-free-core contract
```

If the diff touches an adapter or framework wiring, also run the signature check
(the executable form of the `docs/verified-apis.md §8` verification step, and the nightly-matrix gate):

```bash
python scripts/verify_frameworks.py
```

If you added or touched an adapter, sanity-check that a bare `pip install -e
".[dev]"` + `python -c "import donkey_kit"` still succeeds — that's the
`base-only` CI job catching a framework import that leaked into a lower layer.

**(fork)** GitHub withholds repository secrets from pull requests opened from a
fork, so the secret-gated jobs (the `--live` framework round-trip and anything
reading the `DONKEY_LLM_PROXY_*` env vars) do **not** run on your PR — a
maintainer runs them before merge. Run everything that needs no secrets locally
so the gate is green on what CI *can* check on a fork: `pytest -q tests/unit`,
`mypy`, `ruff check .`, `lint-imports`, and offline `python
scripts/verify_frameworks.py` (no `--live`). The `sandbox` and `local_gateway`
suites need creds/docker you likely don't have; they clean-skip, which is
correct (Section 2). And **never flip a `docs/verified-apis.md` row or `verified=True`
from a fork** — that requires a real sandbox round-trip only a maintainer can
run (verification discipline); raise it in the issue instead.

### The PR

The PR targets `develop` and its body includes `Closes #<issue#>`, a `## Summary`
(with `§N.N` references where relevant), a `## Test plan`, and a mandatory
`## Post-deploy steps` section. That last section is real content in most PRs
(new/changed extras → the `pip install` users need; a value flipped to `VERIFIED`
→ note that `docs/verified-apis.md` moved with it; a new adapter/exemption → note
the README table follow-up) — write `None.` explicitly when nothing applies,
never omit the heading.

Open the PR only once the gate is green — a red PR wastes reviewer attention. If
`develop` advances while the PR is open, rebase (`git rebase origin/develop`) by
default; merge only if a rebase would invalidate in-flight review comments.

### Merging

Merge method is fixed by direction — don't pick per PR:

| Direction | Method | Why |
| --- | --- | --- |
| `<type>/<#>-<slug>` → `develop` | **Squash** | One commit per issue; WIP commits collapse; `git revert <sha>` backs out the whole issue. |
| `develop` → `main` | **Merge commit (no fast-forward)** | Each release is one identifiable, revertable merge commit. |

Never rebase-merge into `develop`, never squash or fast-forward `develop` into
`main`, and never merge `main` back into `develop` (cherry-pick a hotfix onto
`develop` instead). **(fork)** You can't run this step — a maintainer
squash-merges your PR and closes the linked issue with the merge SHA.

After a PR merges, **close the linked issue explicitly** with the merge SHA —
don't rely on GitHub auto-close, which can silently miss. Closing the issue is
what advances its milestone's completed count, which is how release readiness is
tracked. A `develop → main` promotion happens when a milestone reaches **0 open
issues**; the release PR's title carries the milestone and version (e.g.
`Release: Phase 1 — Build the MVP (0.1.0)`).

---

## 2. Testing strategy

The repo has **six distinct test surfaces**, each mapped to a specific CI or
local gate. Getting the surface wrong either weakens a real gate (a framework test
slipped into `tests/unit`) or produces a false negative (a skipped conformance
scenario nobody reviews). Pick by what the change exercises:

| The change exercises… | Surface |
| --- | --- |
| Framework-free logic (`core/`, `registry/`, errors, config, transport) using only httpx + pydantic | **`tests/unit/`** — the `base-only` CI job |
| An adapter's behavior against a fixed scenario set (any of the eight frameworks) | **`tests/conformance/suite.py`** — the conformance kit |
| Behavior pinned to a **real captured** Anypoint request/response | **fixture-driven** test reading `tests/fixtures/anypoint/**` |
| A running local Omni Gateway (docker) | `@pytest.mark.local_gateway` (off by default) |
| A real Anypoint sandbox | `@pytest.mark.sandbox` (off by default, gated by `DONKEY_SANDBOX_TESTS=1`) |
| A framework's constructor signature/kwargs | `scripts/verify_frameworks.py` (not pytest) |
| A downstream package-root import/call combination that source-only analysis cannot exercise | **`tests/typecheck/`** — checked by `mypy`, not pytest |

If a change fits none of these, stop and ask — don't invent a seventh surface.

### `tests/unit/` — the framework-free gate

The `base-only` CI job installs **only** `.[dev]` (no `llm`, no framework
extras), imports `donkey_kit`, then runs `pytest -q tests/unit`. Everything
here must work with zero optional dependencies. **Never add a top-level framework
import to a file under `tests/unit/`** — that's exactly the drift this job
exists to catch. Error-classification changes must keep the taxonomy invariants
provable: `PolicyViolation` stays distinct from the retryable
`UpstreamModelError`, and every `PolicyViolation` carries a non-empty
`remediation` (assert it directly). See [`ARCHITECTURE.md`](ARCHITECTURE.md#error-taxonomy-design-bg-12)
for why.

That job also builds and installs the wheel into an isolated environment, then
verifies the packaged simulator fixtures against their shipped integrity lock.
This is the packaging-path gate; source-checkout tests alone cannot prove those
resources landed in the wheel.

### `tests/typecheck/` — downstream static contracts

`mypy` strict-checks these small, non-pytest modules alongside `src/donkey_kit`.
Use this surface when the contract depends on how a consumer combines public
package-root imports and annotated calls — a composition source-only analysis
cannot exercise. Keep a matching runtime assertion under `tests/unit/` when the
contract also has runtime behavior; a typecheck fixture is not a substitute for
a runtime test.

### The conformance kit — "never a silent skip"

One suite (`python/tests/conformance/suite.py`) runs identically against every
adapter. **A framework is "supported" only if it passes every scenario, or the
scenario is a documented, *asserted* exemption in `KNOWN_LIMITATIONS` with a
specific, falsifiable reason.** There is no `pytest.mark.skip` escape hatch here
— a silent skip is the failure mode this kit exists to prevent, and the
exemptions are published in the README as credibility. When adding an adapter,
wire every scenario; if one genuinely can't pass for a structural reason, add a
`KNOWN_LIMITATIONS` entry (not "not supported yet"). Never add a
framework-specific scenario — a shared-suite invariant must apply to all
frameworks or it doesn't belong there.

### Fixture-driven tests — captures, not conveniences

`tests/fixtures/anypoint/` holds **real captures** from a sandbox org, not
hand-written JSON. The error taxonomy is fixture-derived (BG §1.5), not
assumption-derived. If you need a new response shape, capture it for real and
document its provenance in the relevant `README` (org id, environment,
CLI/proxy version, what produced the rejection, confirmation nothing sensitive
survived) — don't hand-write a synthetic body. Cite the fixture's `§`-section in
the test docstring.

### `local_gateway` and `sandbox` — infra-gated, clean-skip by default

Both markers are declared in `python/pyproject.toml`. `local_gateway` is
exercised by `tests/conformance/test_simulator_boot.py` (and run in CI's `test`
job via `pytest -q -m local_gateway`); `sandbox` is exercised by
`tests/sandbox/` (#400), which calls the real provisioned proxies and is
**never** run in CI — it is opt-in, local only.

- **`@pytest.mark.local_gateway`** needs a local Omni Gateway via docker (the Verification milestone).
  Docker is a hard dependency **for this feature only** — it ships behind the
  optional `[local]` extra; the rest of the SDK works without it. The local
  harness (`Governance.simulate()`) is designed so that **port allocation is
  dynamic**, so parallel test workers don't collide on a fixed port; the fixture
  **prefers gateway config hot-reload over a container restart** between test
  cases; and the whole surface is **gated behind the marker, off by default** —
  never run in plain unit tests. The harness's `env` handle exposes `gateway` (a
  `GatewayTarget` at the dynamically-allocated localhost port), `logs()`, and
  `policy_events(policy_name)` parsed from gateway logs, so a test can assert a
  policy actually fired.
- **`@pytest.mark.sandbox`** needs a real Anypoint sandbox and is gated by
  `DONKEY_SANDBOX_TESTS=1`. The suite (`tests/sandbox/`) calls the LLM Gateway
  proxies provisioned in `donkey-development-kit-provisioning`, each declared in a
  gitignored `proxies.toml` (copy from `proxies.toml.example`) that names a
  `base_url` plus the env vars holding that proxy's consumer creds. See
  `tests/sandbox/README.md`. It is the live twin of the fixture-driven
  `test_llm_proxy_contract.py`, and the path that captures the #253 rejection
  bodies against real proxies.

A test under either marker must **degrade to a clean skip** (not a failure) when
its docker service / env var is absent — that's what "off by default" means. This
is deliberately *different* from the conformance kit's "never skip" rule: these
markers gate *infra availability*, so a clean skip is correct; the conformance
kit gates *framework support*, where a silent skip is not. Run them explicitly
with `pytest -q -m local_gateway` / `-m sandbox` (with the service/env in place).

### `scripts/verify_frameworks.py` — signatures, outside pytest

This is the executable verification-discipline step for adapters' native constructor
signatures (`docs/verified-apis.md` §8): does the exact class we name exist and
accept the exact kwargs we pass, against the framework as actually installed.
`--live` adds one real completion round-trip (needs the three
`DONKEY_LLM_PROXY_*` env vars); `--only <fw>` restricts scope;
`--emit-verified` prints `docs/verified-apis.md §8` markdown rows after maintainer sign-off. A
`_verify.blocked(...)`-guarded adapter correctly shows as `BLOCKED (verification discipline)`, not a
failure — don't "fix" the script to make a genuinely-blocked adapter pass.

### Commands

```bash
# from python/
pip install -e ".[dev,llm,cli]"      # what CI installs
pytest -q                            # full suite
pytest -q tests/unit                 # unit only (the base-only CI job)
pytest -q -m local_gateway           # opt-in local-gateway tests
pytest -q -m sandbox                 # opt-in sandbox tests
python scripts/verify_frameworks.py [--live] [--only <fw>] [--emit-verified]
```

---

## 3. Coding conventions

The full pre-write checklist lives in the `ddk-coding-conventions` skill and the
build plan; the load-bearing rules:

- **`mypy --strict`, blocking.** The whole `src/donkey_kit` tree and the
  downstream public-API contracts under `tests/typecheck/` are strict-checked.
  Annotate every public signature; no untyped defs, no implicit `Any`. Prefer
  `X | None` over `Optional[X]` (ruff `UP` rewrites the old form), and put
  `from __future__ import annotations` at the top of every module (house style).
  Don't silence a real signature mismatch with an unexplained `# type: ignore`
  — the single `[[tool.mypy.overrides]]` block already handles optional/absent
  framework deps.
- **Framework-free core & lazy imports.** `core/` depends on **httpx + pydantic
  only** — no agent framework, ever. Adapters import their framework **lazily,
  inside the method that uses it**, never at module top level; import the
  framework's *types* only under `if TYPE_CHECKING:`. This is what lets
  `import donkey_kit` succeed with no framework installed, and the `base-only`
  job enforces it. The layering (`integrations → tools → registry → llm → core`,
  lower never imports higher) is enforced by `lint-imports`. See
  [`ARCHITECTURE.md`](ARCHITECTURE.md#layered-architecture).
- **Verification guards.** Never invent an endpoint, header, or class
  name. Use `core/_verify.py`: `blocked("…")` where there's no defensible
  placeholder, `Unverified(...)` for an overridable best-guess that warns once.
  Flip `verified=True` **and** the row in `docs/verified-apis.md` together, never
  one without the other. Details in
  [`ARCHITECTURE.md`](ARCHITECTURE.md#verification-discipline).
- **Extras are floors, never ceilings.** No upper version pins in
  `pyproject.toml` — add `foo>=X`, never `foo<Y`. Known incompatibilities are
  documented in `docs/verified-apis.md §8.1` as dev constraints, not encoded as
  pins; the nightly matrix exists to surface breakage from newest releases early.
- **3.10 floor.** `requires-python = ">=3.10"`; CI matrix is 3.10/3.11/3.12.
  `tomllib` is stdlib only on 3.11+, so `tomli` is backfilled below 3.11;
  `typing-extensions` is pulled in below 3.12. Don't use 3.11+ syntax/stdlib
  without a backfill.
- **pydantic v2** idioms (`model_validate`, `Field`, `model_config`); the
  `pydantic.mypy` plugin is on. The package ships `py.typed` (PEP 561) — keep the
  public API fully annotated so downstream users get types.
- **Three ergonomic forms per governed surface** — the `donkey.<framework>`
  factory, a `connection_kwargs()` accessor, and a module-level factory. Keep all
  three when adding an adapter (they must stay in lockstep).
- **`§N.N` citation habit.** When code encodes a build-plan decision, cite the
  section in the docstring/comment so reviewers and future-you can find the
  rationale. A principled deviation gets a leading comment naming the `§N.N` it
  trades against.
- **Trademark-descriptive language (the trademark/support boundary).** "Agent Fabric", "Anypoint", "Omni
  Gateway", and "MuleSoft" are Salesforce trademarks. Write the package as a
  descriptive, third-party SDK for *consuming* Agent Fabric, never as a
  first-party or official Salesforce product.
- **Never commit secrets.** `.donkey-kit.local.toml`, `donkey.lock.local`, and
  `.env` are gitignored. The LLM proxy authenticates on a `client_id`/`client_secret`
  header pair (consumer auth), separate from any Anypoint control-plane credential.

Self-review before pushing = the pre-PR gate in Section 1 (`mypy`, `ruff check .`,
`lint-imports`, `pytest`), plus `verify_frameworks.py` if you touched adapters.

---

## 4. Docs-sync rule

`website/` (Nextra) describes how the SDK behaves from a consumer's
perspective. When code changes what the SDK does — or which platform facts it
depends on — the docs must change *with it*, or the drift is discovered by a
confused adopter instead of at review time. There is no automated drift detector;
this is a PR-time discipline. The full surface→page mapping is in the
`ddk-docs-sync` skill — the load-bearing cases:

| Code surface | Docs page(s) |
| --- | --- |
| `core/errors.py` | `errors.mdx` |
| `core/config.py`, `core/auth.py` | `reference/configuration.mdx` |
| `core/_verify.py`, `docs/verified-apis.md` | `concepts/verification.mdx`, `reference/unsupported-boundary.mdx` |
| `integrations/<fw>.py` | `frameworks/<fw>.mdx` (note `openai_agents.py` → `openai.mdx`) |
| `provisioning/*` | the matching `provisioning/*.mdx` page |
| `README.md` (install/status/extras) | `quickstart.mdx`, `index.mdx` |

`docs/verified-apis.md` is not "engineering-internal" for this purpose: a status
flip there feeds `concepts/verification.mdx`, `reference/unsupported-boundary.mdx`,
and the README status banner — update them together so one page never says "live"
while another still says "planned design."

For every surface a PR touches, do **one** of:

1. **Update the mapped page in the same PR** — re-read it top to bottom against
   the diff and rewrite the affected sections. Preferred; follow-ups decay. Do
   this especially when the delta is mechanical or a developer hitting the merged
   change would otherwise be actively misled.
2. **File a `documentation`-labeled follow-up issue** referencing the PR, titled
   `docs: update <page>.mdx for <change> (follow-up to #<pr#>)`, cross-linked
   from the PR. Use this when the delta needs a whole new page or a separate
   review pass, or when the behavior is still behind a `_verify.blocked(...)`
   guard.

**Merging with neither a docs update nor a linked follow-up is not allowed.** The
same rule extends to [`ARCHITECTURE.md`](ARCHITECTURE.md) and this file: a change
that invalidates something they state must update them in lockstep, or record the
divergence as an intentional decision.

### The one deliberate duplication — and its drift risk

The install + env-var *Configure* block and the per-framework "manual equivalent"
snippet are intentionally duplicated between the consumer docs pages and the
`python/examples/<fw>/README.md` files: the docs page is for *reading*, the
example README is for *running it in place*, and that redundancy is a deliberate
definition-of-done item, not an accident. **The website page is canonical**;
the example READMEs cross-link to it. Because nothing enforces this
correspondence automatically, it is a **manual-sync drift risk**: when you change
an install command, an env-var name, or a framework's construction snippet in one
place, update the paired copy in the same PR. When in doubt, treat the website
page as the source of truth and reconcile the example README to it.

---

*"Agent Fabric", "Anypoint", and "Omni Gateway" are Salesforce trademarks; this
project is a descriptive, non-first-party SDK for consuming those capabilities
(the trademark/support boundary).*

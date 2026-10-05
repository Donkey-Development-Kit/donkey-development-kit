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
owns feature scope (cited `BG §N.N`). A bare `§` label names one of the build
plan's five standing invariants (`§0.3`, `§1.1`, `§2.1`, `§8.1`, `§8.4`); the
archived v1 plan the old bare `§N.N` numbers pointed into was deleted (#266),
so don't add new bare `§` numbers. When a rule here feels arbitrary, read the
cited section.

> **This file is the canonical contributor guide.** Some maintainers use
> AI-agent tooling (prompts, skills) that restates these rules for agents. That
> tooling is optional, is not part of this repository, and never outranks this
> file: if it disagrees with anything here, this file wins and the tooling is
> the bug.

All Python work happens in `python/`; commands below are run from there unless
noted. There is no Makefile — every command runs directly.

> **Found a vulnerability?** Don't open an issue or a PR for it. Report it
> privately as described in [`SECURITY.md`](SECURITY.md).

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
- **Merging and promotion** — squashing a branch into `develop` and promoting
  `develop` → `main` are maintainer-only.
- **Secret-gated CI and live/sandbox verification** — see
  [the pre-PR gate](#the-pre-pr-gate) and [§2](#2-testing-strategy).

**(fork)** **Several PRs against one shared file.** When your contribution
spans multiple PRs that all touch a single shared file — typically
`docs/verified-apis.md` — open them **one at a time, each rebased on
`develop` after the previous merges**, not as a parallel stack. Parallel PRs
against the same file guarantee rebase conflicts and re-review of the same
context. Fold closely related small changes into one PR.

### The issue is the plan

**No code change lands without a GitHub issue and a branch named after it.**
Small changes are exactly where this discipline gets skipped and history gets
unanchored, so there is no "too small for an issue" exception. Plan content
lives in the issue (edit the body or comment) — not in committed `plans/*.md`
scratch files. CI rejects any tracked file under a `plans/` directory in
`docs/` (`scripts/check_doc_links.py`).

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
   Projects board. The issue's type label (`enhancement`, `bug`,
   `documentation`, `chore`, `breaking-change`) is copied onto the PR that
   closes it and decides its section in the release notes, so keep `Closes #N`
   in the PR body ([`docs/releasing.md`](docs/releasing.md)). **(fork)** File
   the issue, but leave milestone/labels/assignee to a maintainer — setting
   them needs write access; note in the issue that you plan to work it.
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
   Commit messages cite the spec (`BG §N.N`, an invariant, or a `Phase N`) when
   the change implements or modifies spec-governed behavior, e.g.
   `fix(llm): correct proxy base URL handling (§2.1)`.

**Use a worktree when there's any chance of a parallel session** (another editor
window, a running dev server, a `pytest --looponfail` holding files): one issue =
one branch = one worktree. A fresh worktree has no installed venv/extras — run
`pip install -e ".[llm,cli]" --group dev` in its `python/` before testing.

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
ruff check .       # rule families in pyproject.toml (see the §3 map); line-length 100
lint-imports       # the layered, framework-free-core contract
vulture            # dead code in src/; allowed names in vulture_whitelist.py
```

The secret scan runs outside `python/`, in its own `secret-scan` CI job (gitleaks
over the full history, configured in `.gitleaks.toml`). Install the matching
commit hook once per clone so a secret is caught before it is committed:

```bash
pipx install pre-commit   # or: pip install pre-commit
pre-commit install        # from the repository root; runs gitleaks on staged changes
```

If the diff touches an adapter or framework wiring, also run the signature check
(the executable form of the `docs/verified-apis.md §8` verification step, and the nightly-matrix gate):

```bash
python scripts/verify_frameworks.py
```

If the diff adds, removes, or re-pins a dependency (`pyproject.toml`,
`dependency_allowlist.toml`), refresh the PR-CI lock in the same PR (ADR 0007
rule 3 — [§3](#3-coding-conventions) above has the full rule):

```bash
python scripts/compile_constraints.py   # needs `uv` on PATH; writes constraints/<combo>-py3.NN.txt
```

If you added or touched an adapter, sanity-check that a bare `pip install -e .
--group dev` + `python -c "import donkey_kit"` still succeeds — that's the
`base-only` CI job catching a framework import that leaked into a lower layer.

**(fork)** GitHub withholds repository secrets from pull requests opened from a
fork, so the secret-gated jobs (the `--live` framework round-trip and anything
reading the `DONKEY_LLM_PROXY_*` env vars) do **not** run on your PR — a
maintainer runs them before merge. Run everything that needs no secrets locally
so the gate is green on what CI *can* check on a fork: `pytest -q tests/unit`,
`mypy`, `ruff check .`, `lint-imports`, and offline `python
scripts/verify_frameworks.py` (no `--live`). The `sandbox` suite needs a real
Anypoint sandbox you likely don't have, and `local_gateway` needs the optional
`[local]` extra installed (no Docker, no Omni/Flex Gateway — donkey-development-kit
does not support Local Mode as a test surface, #661); both clean-skip when their
prerequisite is absent, which is correct (Section 2). And **never flip a `docs/verified-apis.md` row or promote an `Unverified(...)`
placeholder from a fork** — that requires a real sandbox round-trip only a maintainer can run
(verification discipline). What you *can* contribute from a fork is the
**fixture** for a new shape: capture it in your own sandbox following the
provenance and byte-exact rules in
[§2](#fixture-driven-tests--captures-not-conveniences), and propose the ledger
row **left `UNVERIFIED`**, noting in the PR that the capture came from a
contributor sandbox rather than the DDK team sandbox. A maintainer then
re-captures the same shape on the team sandbox and flips the row — your
capture is what makes that fast, but it is not itself the verification.

### The PR

The PR targets `develop` and its body includes `Closes #<issue#>`, a `## Summary`
(with `BG §N.N` / invariant references where relevant), a `## Test plan`, and a mandatory
`## Post-deploy steps` section. That last section is real content in most PRs
(new/changed extras → the `pip install` users need; a value flipped to `VERIFIED`
→ note that `docs/verified-apis.md` moved with it; a new adapter/exemption → note
the README table follow-up) — write `None.` explicitly when nothing applies,
never omit the heading. The repo's PR template
([`.github/pull_request_template.md`](.github/pull_request_template.md)) lays
these sections out, plus the **ADR needed?** checkbox below.

Open the PR only once the gate is green — a red PR wastes reviewer attention. If
`develop` advances while the PR is open, rebase (`git rebase origin/develop`) by
default; merge only if a rebase would invalidate in-flight review comments.

### Architecture decision records

Design decisions are recorded as ADRs in [`docs/adr/`](docs/adr/README.md),
one file each: the context, the decision, the alternatives rejected, the
consequences, and a status (proposed, accepted, superseded or rejected). **A PR
needs an ADR when it changes an `ARCHITECTURE.md` invariant, an import-linter
contract, the API stability tiers or the dependency policy.** It adds a new
ADR, or one that supersedes an accepted ADR, since an accepted decision isn't
edited. Correcting a description so it matches what the code already does
needs none. The PR template's **ADR needed?** checkbox asks on every PR, and
a reviewer can ask for one when the answer is wrong. The process, the template
and the index are in [`docs/adr/README.md`](docs/adr/README.md).

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
| Framework-free logic (`core/`, `registry/`, errors, config, transport) with no agent framework installed | **`tests/unit/`** — the `base-only` CI job |
| An adapter's behavior against a fixed scenario set (any of the eight frameworks) | **`tests/conformance/suite.py`** — the conformance kit |
| Behavior pinned to a **real captured** Anypoint request/response | **fixture-driven** test reading `tests/fixtures/anypoint/**` (test-only captures) or `src/donkey_kit/simulator/_fixtures/**` (captures the simulator ships) |
| The pure-Python local gateway simulator (`donkey mock`, no Docker) | `@pytest.mark.local_gateway` (off by default) |
| A real Anypoint sandbox | `@pytest.mark.sandbox` (off by default, gated by `DONKEY_SANDBOX_TESTS=1`) |
| A framework's constructor signature/kwargs | `scripts/verify_frameworks.py` (not pytest) |
| A downstream package-root import/call combination that source-only analysis cannot exercise | **`tests/typecheck/`** — checked by `mypy`, not pytest |

If a change fits none of these, stop and ask — don't invent a seventh surface.

### `tests/unit/` — the framework-free gate

The `base-only` CI job installs **only** the base package and the `dev`
dependency group (no `llm`, no framework extras), imports `donkey_kit`, then runs `pytest -q tests/unit`. Everything
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

### Adding a framework adapter: the integration checklist

An adapter is one entry in `ADAPTERS` (`src/donkey_kit/integrations/__init__.py`)
plus everything below. `tests/unit/test_integration_checklist.py` checks each
item per registry entry, so a new entry that skips one fails CI.

1. **A registry entry.** Its `probe` is a tuple of every module the factories
   need (including a dependency the framework doesn't always install), and its
   `extra` names the pip extra.
2. **An extra with a floor.** Each requirement is `>=` the lowest verified
   version, including any sub-extra the adapter needs (`strands-agents[openai]`).
   It has a matching row in `docs/verified-apis.md` §8 and a §8.3 floor row.
3. **A lazy import.** The adapter module never imports its framework at module
   level (only under `TYPE_CHECKING`). Its factories import through
   `Adapter._native_import`, which raises the curated `missing_framework_error`.
4. **All three forms.** `donkey.<fw>.<factory>()`, `donkey.<fw>.connection_kwargs()`
   and the module-level `donkey_kit.integrations.<fw>.<factory>()`.
5. **The surrounding artifacts.** A `scripts/verify_frameworks.py` row per
   factory, a `website/content/frameworks/` page (the adapter docstring's
   `Docs:` link), an `examples/<fw>/main.py` that exposes `build(donkey)` (one
   governed call through the framework's own entry point, modelled on
   `examples/langgraph/main.py`; `test_example_build_passes_the_conformance_kit`
   runs it through `run_conformance`, with a module-level `KNOWN_LIMITATIONS`
   only where `suite.py` records a structural limit), and an entry in the import-linter independence contract in
   `pyproject.toml`.
6. **The adapter contract suite, run with the real framework in CI.** That
   means a driver per factory in `tests/conformance/contract_drivers.py`, a
   case in `test_framework_retries.py` and `test_missing_framework_error.py`,
   and the extra in the `adapter-contract` matrix in `.github/workflows/ci.yml`.
   The suite asserts these things:
   - governed headers and the run's correlation id;
   - exactly one send on a 429 or 403;
   - a typed refusal;
   - streamed and sync calls;
   - a call after the framework closes its client;
   - `last_call` matching `observes_last_call`;
   - the curated missing-framework error.

Wire the adapter by these rules:

- **Use the shared transport.** Hand the framework a non-owning view
  (`self.http_client()` / `self.sync_http_client()`), never a client it can
  close for everyone. Header-only wiring needs a structural reason, recorded in
  `KNOWN_LIMITATIONS`.
- **Turn the framework's own retries off.** The shared transport is the one
  retry layer, so a refusal is sent once.
- **Wire both sync and async**, or let the driver's `no_sync` say why the
  framework is async-only.
- **Use the shared typed-refusal bridge** (`core/refusals.py`, ADR 0002) rather
  than a per-framework copy. A framework that wraps the openai error in a type
  carrying no request or response gets an `AdapterSpec.refusal_translator`.
- **`observes_last_call` is the single source of truth** for whether
  `donkey.last_call` sees the adapter's calls. It holds for every factory and is
  never changed on an instance.

### Fixture-driven tests — captures, not conveniences

`tests/fixtures/anypoint/` (test-only captures) and
`src/donkey_kit/simulator/_fixtures/` (the captures `simulate()`, `donkey mock`
and the `gateway` fixture replay, shipped in the wheel since #944) hold **real
captures** from a sandbox, not hand-written JSON. The error taxonomy is fixture-derived (BG §1.5), not
assumption-derived. If you need a new response shape, capture it for real — 
never hand-write a synthetic body — and cite the fixture's `§`-section in the
test docstring.

**A live capture is recorded as exactly three things:** the fixture files;
the regenerated integrity lock (`python -m donkey_kit.simulator.fixtures
--relock` — the reviewable "I re-captured this, I meant it" step); and one
row in the `docs/verified-apis.md` ledger carrying the date, the instance ID,
and the policy + gateway version. Nothing else belongs in the repo — no
`docs/evidence/<issue>/` folders, and no committed local gate logs (pytest /
mypy / ruff / import-linter output); CI is the record for those. Deployment,
teardown, and inventory receipts go in the **PR description**, not a committed
file.

**Both fixture trees are integrity-locked, and editing either without
relocking fails CI (#752).** `python -m donkey_kit.simulator.fixtures
--relock` regenerates **two** lock files in one run:
`src/donkey_kit/simulator/_fixtures/fixtures.lock` (the ~25 files the
simulator serves; package data, shipped in the wheel) and
`tests/fixtures/fixtures.lock` (every file under `tests/fixtures/`; test-only,
never shipped — `tests/unit/test_fixture_integrity.py` pins that boundary too).
`--relock` is the **only** way to update either lock: a hand-edited fixture
byte with no matching lock change fails
`tests/unit/test_fixture_integrity.py` loudly, naming the drifted
file. There is no bypass flag.

**One command does the whole capture.** `scripts/capture_fixture.py` sends one
HTTP request, writes the `*.headers.txt` / `*.body.<ext>` pair, appends a
provenance line to the target directory's `README.md`, then runs the scrub and
`--relock` steps below for you:

```bash
python scripts/capture_fixture.py \
    --out-dir tests/fixtures/anypoint/model_wallet \
    --name responses.success \
    --method POST --url https://<sandbox-host>/v1/chat/completions \
    --header "client_id: $DONKEY_LLM_PROXY_CLIENT_ID" \
    --header "client_secret: $DONKEY_LLM_PROXY_CLIENT_SECRET" \
    --data '{"model": "openai/gpt-5-mini", "messages": [...]}' \
    --provenance "Captured 2026-10-05 against sandbox instance 123, model-wallet policy v2."
```

This is a **by-hand developer tool, not a CI step**: it makes one real request
to whatever `--url` names, so only a maintainer capturing a real sandbox
response runs it, never an automated agent against a live endpoint. Its own
tests (`tests/unit/test_capture_fixture.py`) exercise every code path offline
through `httpx.MockTransport`. `--out-dir` must sit under `tests/fixtures/` or
`src/donkey_kit/simulator/_fixtures/`, the two trees it scrubs and relocks,
and the script refuses a directory outside them before it sends anything. It
requests `Accept-Encoding: identity` so the body it writes is the bytes on the
wire, and it refuses to write a compressed response. After it runs, review the
diff and confirm `scrub_fixtures.py --check` is still clean before committing
the fixture pair, the `README.md` update, and both regenerated locks together.
The script does not touch `docs/verified-apis.md`. If the capture verifies a
shape, flip its ledger row by hand.

Record **how to reproduce the shape** in the fixture index
(`python/src/donkey_kit/simulator/_fixtures/rejections/README.md`): one line giving the trigger
input and the policy configuration that produced it. If what you observed
differs from what the platform documents, record the discrepancy in the
ledger row (e.g. "schema documents 403, gateway returned 400") — the ledger
tracks reality, not the spec.

**No personal attribution in committed files.** Don't name contributors or
their organizations anywhere in the repo; the PR and git history record
authorship. Describe a non-team environment neutrally (e.g. "a contributor
Anypoint sandbox, not the DDK team sandbox, instance `NNN`") and **omit
organization/environment UUIDs and gateway hostnames**. This is why fixture
provenance records a neutral environment description plus the instance ID —
not an org id.

**Scrub every capture before you commit it.** A raw capture carries the
capturing tenant's identifiers: organization, environment and asset UUIDs,
correlation ids, gateway and identity-provider hostnames, and the upstream
provider's account headers (`openai-organization`, `openai-project`,
`anthropic-workspace-id`). Some fixtures ship in the wheel, and a PyPI release
can't be changed afterwards. So the procedure is **capture → scrub → relock**:

```bash
python scripts/scrub_fixtures.py tests/fixtures src/donkey_kit/simulator/_fixtures   # rewrite in place
python -m donkey_kit.simulator.fixtures --relock
```

Name both fixture trees. With no path the script scans only the files git
already tracks, so it skips a capture you have not yet added.

The scrub is deterministic: the same real value always maps to the same
placeholder (`00000000-0000-4000-8000-…`, `<name>.example.invalid`), so
cross-file references stay consistent. It also leaves every other byte alone,
so byte-exact captures stay byte-exact. The numeric API instance ID is kept: it
is the provenance the ledger row cites. CI runs `scrub_fixtures.py --check`
over every tracked file and over the built wheel and sdist, and fails on any
UUID outside the script's `ALLOWED_UUIDS`, any `*.cloudhub.io` or
`*.herokuapp.com` host, or any unscrubbed provider account header. A UUID that
is genuinely public goes in `ALLOWED_UUIDS`, with a reason.

**Byte-exact captures.** When a capture must keep its exact bytes — CRLF in a
`.headers.txt`, no trailing newline — add a **narrow** `.gitattributes` entry
marking just those paths **`-text`** (never `binary`, which makes re-captures
undiffable in review). `-text` disables newline normalization while keeping
the file reviewable as text.

### `local_gateway` and `sandbox` — infra-gated, clean-skip by default

Both markers are declared in `python/pyproject.toml`. `local_gateway` is
exercised by `tests/conformance/test_simulator_boot.py` (and run in CI's `test`
job via `pytest -q -m local_gateway`); `sandbox` is exercised by
`tests/sandbox/` (#400), which calls the real provisioned proxies and is
**never** run in CI — it is opt-in, local only.

- **`@pytest.mark.local_gateway`** boots the pure-Python local gateway simulator
  (BG §1.4) on a real TCP port — it needs the optional `[local]` extra
  (Starlette + Uvicorn) and nothing else; there is no Docker dependency, and
  donkey-development-kit does not support Omni/Flex Gateway Local Mode as a
  test surface (#661). Bind the simulator to a dynamically allocated port so
  parallel test workers don't collide, and keep the surface **gated behind the
  marker, off by default** — never run in plain unit tests.
  `donkey.simulate()` is **not** this harness: it injects a captured refusal
  in process, with no server and no port, so a plain unit test can use it.
- **`@pytest.mark.sandbox`** needs a real Anypoint sandbox and is gated by
  `DONKEY_SANDBOX_TESTS=1`. The suite (`tests/sandbox/`) calls the LLM Gateway
  proxies provisioned in `donkey-development-kit-provisioning`, each declared in a
  gitignored `proxies.toml` (copy from `proxies.toml.example`) that names a
  `base_url` plus the env vars holding that proxy's consumer creds. See
  `tests/sandbox/README.md`. It is the live twin of the fixture-driven
  `test_llm_proxy_contract.py`, and the path that captures the #253 rejection
  bodies against real proxies.

A test under either marker must **degrade to a clean skip** (not a failure) when
its prerequisite (the `[local]` extra / the env var) is absent — that's what "off by default" means. This
is deliberately *different* from the conformance kit's "never skip" rule: these
markers gate *infra availability*, so a clean skip is correct; the conformance
kit gates *framework support*, where a silent skip is not. Run them explicitly
with `pytest -q -m local_gateway` / `-m sandbox` (with the extra / env var in place).

### `scripts/verify_frameworks.py` — signatures, outside pytest

This is the executable verification-discipline step for adapters' native constructor
signatures (`docs/verified-apis.md` §8): does the exact class we name exist and
accept the exact kwargs we pass, against the framework as actually installed.
`--live` adds one real completion round-trip (needs the three
`DONKEY_LLM_PROXY_*` env vars). The `adk.gemini` row needs a `Format=Gemini`
proxy, so its live check runs only when `DONKEY_GEMINI_PROXY_URL` is also set,
with a credential pair contracted on that proxy; otherwise it is skipped.
`--only <fw>` restricts scope (`--only adk` covers `adk` and `adk.gemini`);
`--emit-verified` prints `docs/verified-apis.md §8` markdown rows after maintainer sign-off. A
`_verify.blocked(...)`-guarded adapter correctly shows as `BLOCKED (verification discipline)`, not a
failure — don't "fix" the script to make a genuinely-blocked adapter pass.

### Commands

```bash
# from python/
pip install -e ".[llm,cli]" --group dev   # what CI installs (pip 25.1+)
pytest -q                            # full suite
pytest -q tests/unit                 # unit only (the base-only CI job)
pytest -q -m local_gateway           # opt-in local-gateway tests
pytest -q -m sandbox                 # opt-in sandbox tests
python scripts/verify_frameworks.py [--live] [--only <fw>] [--emit-verified]
```

---

## 3. Coding conventions

Walk this checklist before writing code under `python/src/donkey_kit/`; the
build plan has the rationale behind each rule:

- **`mypy --strict`, blocking.** The whole `src/donkey_kit` tree and the
  downstream public-API contracts under `tests/typecheck/` are strict-checked.
  Annotate every public signature; no untyped defs, no implicit `Any`, and an
  explicit `Any` only for a `*args`/`**kwargs` pass-through or an optional-dep
  seam listed in `pyproject.toml` (ruff `ANN401`). Prefer
  `X | None` over `Optional[X]` (ruff `UP` rewrites the old form), and put
  `from __future__ import annotations` at the top of every module (ruff `I002`).
  Don't silence a real signature mismatch with an unexplained `# type: ignore`
  — the single `[[tool.mypy.overrides]]` block already handles optional/absent
  framework deps.
- **Framework-free core & lazy imports.** `core/` depends on **httpx only** (the
  build plan allows pydantic too, but core imports none) — no agent framework,
  ever. Adapters import their framework **lazily,
  inside the method that uses it**, never at module top level; import the
  framework's *types* only under `if TYPE_CHECKING:`. This is what lets
  `import donkey_kit` succeed with no framework installed, and the `base-only`
  job enforces it. The layering (`integrations → tools → registry → llm → core`,
  lower never imports higher) is enforced by `lint-imports`, which also orders
  the rest of the package (the CLI, the dev-only siblings, `donkey` and
  `experimental`) and the modules inside `core/`. Both layer
  contracts are exhaustive: a new top-level module or core module is placed in
  `pyproject.toml` in the same PR. No library module imports the CLI. See
  [`ARCHITECTURE.md`](ARCHITECTURE.md#layered-architecture).
- **Small core modules.** A module in `core/` stays within 500 lines. The four
  already past it are held at their current size and may only shrink: lower a
  ratchet ceiling in the PR that shrinks the file, and drop the entry once it is
  within budget (`_OVERSIZED_CORE_MODULES` in `tests/unit/test_architecture.py`).
- **Verification guards.** Never invent an endpoint, header, or class
  name. Use `core/_verify.py`: `blocked("…")` where there's no defensible
  placeholder, `Unverified(...)` for an overridable best-guess that warns once.
  When a placeholder is confirmed, flip its row in `docs/verified-apis.md` **and**
  replace it with a plain constant together, never one without the other.
  Outside `core/_verify.py`, cite the ledger (`docs/verified-apis.md §N`) rather
  than restating a status or date; `scripts/check_verification_claims.py`
  enforces this in CI. Details in
  [`ARCHITECTURE.md`](ARCHITECTURE.md#verification-discipline).
- **Extras are floors, never ceilings.** No upper version pins in
  `pyproject.toml` — add `foo>=X`, never `foo<Y`. Known incompatibilities are
  documented in `docs/verified-apis.md §8.1` as dev constraints, not encoded as
  pins; the nightly matrix exists to surface breakage from newest releases early.
  Each floor is the lowest verified release (`docs/verified-apis.md §8.3`).
  [ADR 0007](docs/adr/0007-dependency-policy.md) records the dependency
  policy. This does not apply to `python/constraints/*.txt` (below): those
  are exact, CI-only pins for reproducibility, not a `pyproject.toml` ceiling.
- **PR CI resolves from a lock (ADR 0007 rule 3).** The PR-gating jobs in
  `.github/workflows/ci.yml` install with
  `-c python/constraints/<combo>-py3.NN.txt` — one file per job's own exact
  extras/dev-group/Python combo, **never** a file merged or shared across
  combos — so a PR goes red only because of what it changed, never because an
  upstream package released that day. One file per combo, not per Python
  version, is load-bearing: `openai-agents`, `google-adk`, `crewai`, and
  `llama-index-llms-openai(-like)` each put a ceiling on a package
  (`websockets`, `regex`, `openai`) that a *different* combo's own compile
  resolves past, so a file merged across combos is unsatisfiable for whichever
  combo's ceiling it crosses (this shipped broken once, see
  `scripts/compile_constraints.py`'s module docstring — don't reintroduce
  merging to "simplify" the file count). The jobs that are deliberately a
  fresh resolve (`all-extra-resolves`, `adk-stacks`, `anthropic-stacks`, and
  everything in `.github/workflows/nightly-matrix.yml`, the canary ADR 0007
  rule 4 relies on) have no `-c` flag and must keep none. **Refresh the lock**
  after changing a dependency, or on whatever cadence a maintainer judges
  useful — there is no scheduled job that does this automatically (opening a
  PR from a scheduled workflow needs write-access credentials this repo
  doesn't grant one, so it stays a manual step):
  ```bash
  cd python
  python scripts/compile_constraints.py   # needs `uv` on PATH (dev-time only; CI still uses plain pip)
  git diff constraints/                   # review before committing
  ```
  `python scripts/compile_constraints.py --check` recompiles every combo and
  exits 1 without writing if any file would change; because each combo is
  compiled on its own, a clean `--check` run also proves every committed file
  is still satisfiable for its own combo (an unsatisfiable combo makes `uv pip
  compile` fail outright, not produce a diff). `tests/unit/test_constraints_lock.py`
  is the cheap offline check that every combo's own direct dependencies are
  pinned in its own file, every `Combo` has its file(s) committed, every
  committed file maps back to a `Combo`, and every `adapter-contract` matrix
  leg has a matching `Combo` — catches drift between `_COMBOS`,
  `pyproject.toml`, and `ci.yml`, not a stale version (that needs the script's
  live `--check`). If you add a new extra or framework to a PR-gating job, add
  its combo to `_COMBOS` in `scripts/compile_constraints.py`, matching that
  job's install line's extras and `--group dev` flag exactly, in the same PR
  and refresh.
- **Every direct dependency is a reviewed decision.** Adding a package to
  `dependencies`, an extra, or a dependency group means adding its entry to
  `python/dependency_allowlist.toml` (why it is needed, and the review date) in
  the same PR, and removing a package means removing its entry. On a PR that
  adds a name, CI also checks that the project exists on PyPI and warns when it
  is young, abandoned, or one or two characters off another allowlisted name.
- **3.10 floor.** `requires-python = ">=3.10"`; CI matrix is 3.10/3.11/3.12,
  and the version classifiers match it. When the floor moves is set by
  [`docs/python-support.md`](docs/python-support.md).
  `tomllib` is stdlib only on 3.11+, so `tomli` is backfilled below 3.11;
  `typing-extensions` is pulled in below 3.12. Don't use 3.11+ syntax/stdlib
  without a backfill.
- **Value objects are frozen dataclasses** (`@dataclass(frozen=True)`), not
  pydantic models: config, catalog entries, registry assets and results. Change
  one by building a new instance (`dataclasses.replace(...)`, or a typed helper
  such as `DonkeyConfig.with_overrides(...)`), and type keyword-override bags
  with `TypedDict` + `Unpack`. A plain `@dataclass` is only for mutable internal
  state. pydantic is used only at an external-schema boundary, where the SDK
  validates a document it doesn't control. No module has one today, so
  `pydantic` is not a base dependency and the `pydantic.mypy` plugin is off;
  `tests/unit/test_base_install_deps.py` and the `base-only` job keep it that
  way (#730). See
  [ADR 0001](docs/adr/0001-value-objects.md). The package ships `py.typed`
  (PEP 561) — keep the public API fully annotated so downstream users get types.
- **Public API surface.** Every public module (no `_` in its path) declares a
  sorted `__all__` (ruff `RUF022`). Every SDK type in a public `Donkey`
  signature is exported from `donkey_kit` (or `donkey_kit.core`), and no class
  name means two different types across those `__all__` lists
  (`tests/unit/test_public_api_surface.py`). A type that exists only for a
  verification-blocked surface goes in `donkey_kit.experimental`, not
  `donkey_kit.__all__` (#730); a name moved there keeps a deprecated
  `donkey_kit.<name>` alias for a release. Submodule paths are not API. Don't
  reach into another object's private members (ruff `SLF001`): the dev-only
  simulator and conformance siblings go through `donkey_kit._testing`, and each
  remaining exception carries a `# noqa: SLF001` with its reason. No top-level
  package imports another's private names (`from ..core.config import _X`), or
  a private module other than the named seams (`core._verify`, `core._wire`,
  `_testing`, `integrations._base`).
- **Three ergonomic forms per governed surface** — the `donkey.<framework>`
  factory, a `connection_kwargs()` accessor, and a module-level factory. Keep all
  three when adding an adapter (they must stay in lockstep). Hand the framework
  the SDK's shared client wherever its constructor takes one, and pass every
  URL override the factory accepts through `_allow_endpoints(...)` before the
  framework import, so it gets the https check and joins the checked endpoints.
- **One adapter contract, one roster** (#726, ADR 0004). An adapter declares
  `factories`, a read-only mapping from each factory method's name to its
  frozen `AdapterCapabilities`, with the default factory first. Every
  `connection_kwargs()` builds on `self._connection()` (pass the factory name
  for a non-default factory), never on `cfg.validated(...)` directly, so the
  token-mode guards run for it. The adapter set is declared once, in
  `ADAPTERS`. Adding an adapter means a roster entry plus the places
  `tests/unit/test_adapter_roster.py` checks against it: a pyproject extra, the
  import-linter independence contract, a mypy override for the framework, a
  `Donkey` annotation, a `scripts/verify_frameworks.py` row per factory, an
  `examples/<name>/main.py`, and `KNOWN_LIMITATIONS` rows that match its
  capabilities.
- **No classification in an adapter** (#724, ADR 0002). Typed refusals reach
  the caller through the one bridge in `core/refusals.py`
  (`donkey_kit.typed_refusals()`, applied by `donkey.run()` and
  `@donkey.governed`). An adapter never calls `classify()` itself. If the
  framework wraps the client's error in an exception with no request or
  response on it, add a module-level translator that only unwraps it, by
  handing its cause back to `core.refusals.translate()`, and name it in the
  adapter's `AdapterSpec.refusal_translator`. Import the framework lazily
  inside the translator.
- **Citation habit.** When code encodes a spec decision, cite it in the
  docstring/comment so reviewers and future-you can find the rationale: `BG §N.N`
  for build-guide scope (e.g. `# budget parsed at the response hook (BG §1.3)`),
  a standing invariant by its `§` label or name (`§1.1`, verification
  discipline), or a `Phase N`. A principled deviation gets a leading comment
  naming what it trades against.
- **Docstrings on every public symbol.** Each public class, method and function
  in `src/` has a docstring (ruff `D101`-`D103`, blocking; tests, examples and
  scripts are exempt). Say what it does, its key parameters and the errors it
  raises (Google-style `Args:` / `Raises:`), and on a main entry point link its
  page on <https://docs.donkey-kit.dev/>, since that is what `help()` and an IDE
  hover show.
- **Trademark-descriptive language (the trademark/support boundary).** "Agent Fabric", "Anypoint", "Omni
  Gateway", and "MuleSoft" are Salesforce trademarks. Write the package as a
  descriptive, third-party SDK for *consuming* Agent Fabric, never as a
  first-party or official Salesforce product.
- **Never commit secrets.** `.donkey-kit.local.toml`, `donkey.lock.local`, and
  `.env` are gitignored. Put secrets in `.donkey-kit.local.toml` (the SDK
  merges it key by key into `.donkey-kit.toml`) or in environment variables, never in the
  committed `.donkey-kit.toml`; the SDK warns if it finds one there. A URL read
  from either working-directory file only receives credentials from those same
  files (loopback hosts included), unless `DONKEY_TRUST_PROJECT_CONFIG=1` is set (see
  `website/content/reference/configuration.mdx`). In `jwt` mode an
  `llm_proxy_url` from those files always needs the environment or the opt-in,
  because the JWT never comes from a file. Both working-directory files must be
  regular files or links that stay inside the working directory. The LLM proxy authenticates on
  a `client_id`/`client_secret` header pair (consumer auth), separate from any
  Anypoint control-plane credential.
  Local tooling config is gitignored too: `.mcp.json`,
  `.claude/settings.local.json` and the `artifacts/` run-output directory. To
  share an MCP client config, commit it under another name (for example
  `.mcp.json.example`) and reference credentials through `${ENV_VAR}`
  interpolation, never inline values. gitleaks scans every commit (pre-commit
  hook and the `secret-scan` CI job), and GitHub push protection is on. To
  silence a false positive, allowlist the exact synthetic value in
  `.gitleaks.toml`, never a path.
- **No dead parameters or stale suppressions.** An argument a function never
  reads is removed (ruff `ARG`); when an override, protocol or blocked stub fixes
  the signature, it stays with a `# noqa: ARG00x` naming that API, or with a
  `per-file-ignores` entry for a module of blocked stubs; a `# noqa` that suppresses nothing is deleted (ruff `RUF100`); and an
  `except` that only re-raises is dropped (ruff `TRY203`).
- **Error contract.** Every `DonkeyError` subclass ships its own non-empty
  default `remediation` (overridable, never blank) and is exported from
  `donkey_kit`, as is `classify`; an error only a verification-blocked surface
  raises is exported from `donkey_kit.experimental` instead (#730). A domain failure raises a `DonkeyError`
  subclass, never a builtin exception.
- **One source of truth, no dead code.** Gateway header names live only in
  `core/_wire.py`; import them, never retype the string. Each env var is named
  once, in `core/config.py`'s field table. Code nothing in `src/` uses is
  deleted (`vulture`); a name used only from outside `src/` goes in
  `vulture_whitelist.py` (its docstring says when), and a public symbol is
  deprecated with a `DeprecationWarning` before it is removed.

### Rule-to-enforcement map

The style guide is the config (#721): every rule above is enforced by a tool or
a test in the pre-PR gate, or is marked review-only. When you add a
rule, add its row; a rule that nothing can check is a review note, not a rule.

| Rule | Enforced by | Where it runs |
| --- | --- | --- |
| `mypy --strict`, annotated public API | mypy `strict = true` over `src/donkey_kit` and `tests/typecheck/` | `typecheck-and-lint`: `mypy` |
| Examples type-check too | mypy `--strict`, one `python/examples/` directory per run | `typecheck-and-lint` (LangGraph: `langgraph-demo`) |
| No explicit `Any` outside listed seams | ruff `ANN401` (`allow-star-arg-any`; seams in `per-file-ignores`) | `ruff check .` |
| No unexplained `# type: ignore` | mypy `warn_unused_ignores` (part of `strict`) catches stale ones; the explanation is review-only | `mypy` |
| `X \| None`, PEP 585/604 syntax | ruff `UP`, `FA` | `ruff check .` |
| `from __future__ import annotations` in every module | ruff `I002` (`isort.required-imports`) | `ruff check .` |
| Framework-free core, layering | import-linter contracts in `pyproject.toml` (whole-package and in-core layers, both exhaustive; the CLI on top, so nothing imports it; the root package loads only production layers; core's forbidden third-party packages, #729); `tests/unit/test_architecture.py` (core's third-party imports as an allowlist, imports inside functions included; `import donkey_kit` in a fresh interpreter loads no dev-only module or the CLI) | `lint-imports`, `pytest` |
| Small core modules (#729) | `tests/unit/test_architecture.py` (500-line budget; a ratchet for the four modules already past it) | `pytest` |
| Lazy framework imports | `import donkey_kit` and `tests/unit` with no extras installed | `base-only` job |
| Verification guards | `scripts/check_verification_claims.py` (no status claims outside `core/_verify.py`); not inventing a value is review-only | `typecheck-and-lint` |
| Extras are floors, never ceilings | `tests/unit/test_house_style_config.py` (only `>=`/`!=` specifiers) | `pytest` |
| Every direct dependency is a reviewed decision (#936) | `tests/unit/test_house_style_config.py` (every declared name has a `dependency_allowlist.toml` entry with a reason and date, and no stale entry); `scripts/check_new_dependencies.py` (a new name exists on PyPI; age, staleness and lookalike warnings) | `pytest`; `new-dependencies` (PRs) |
| 3.10 floor | `requires-python`, ruff `target-version = "py310"`, mypy `python_version = "3.10"`, the 3.10 leg of the `test` matrix | `ruff`, `mypy`, `test` |
| `py.typed` shipped | `py.typed` presence in `tests/unit/test_house_style_config.py` | `pytest` |
| Value objects are frozen dataclasses; pydantic only at external-schema boundaries (#723) | Review-only: ADR 0001 records the decision; no tool checks it. That no base module imports pydantic is checked by `tests/unit/test_base_install_deps.py` and the `base-only` job | review, `pytest`, `base-only` job |
| An ADR for any change to an `ARCHITECTURE.md` invariant, an import-linter contract, the API stability tiers or the dependency policy (#731) | Review-only: the **ADR needed?** checkbox in `.github/pull_request_template.md`; process in `docs/adr/README.md` | review |
| Three ergonomic forms per adapter | `tests/unit/test_adapter_ergonomics.py`, `tests/unit/test_adapter_capabilities.py` (every declared factory over the whole roster) | `pytest` |
| One adapter contract, one roster (#726) | `tests/unit/test_adapter_capabilities.py` (`AdapterProtocol`, frozen per-factory capabilities that match the governed kwargs, every `connection_kwargs()` through `_connection()`, token-mode refusals); `tests/unit/test_adapter_roster.py` (`ADAPTERS` against the extras, import-linter, mypy overrides, `Donkey` annotations, exemptions, nightly matrix, examples, `verify_frameworks.py`) | `pytest` |
| No classification in an adapter (#724) | `tests/unit/test_refusal_bridge.py` (no adapter module calls `classify(`; every adapter exposes the shared bridge) | `pytest` |
| Citation habit | Review-only: no tool can tell whether a comment should cite a spec section | review |
| Trademark-descriptive language | Review-only | review |
| Never commit secrets | `.gitignore` entries; the committed-file secret warning in `tests/unit/test_config_endpoint_trust.py`; gitleaks (`.gitleaks.toml`); GitHub push protection | `pytest`, `secret-scan`, pre-commit hook, `git push` |
| No tenant identifiers in tracked files or built dists (#821) | `scripts/scrub_fixtures.py --check` (UUIDs outside `ALLOWED_UUIDS`, platform hosts, provider account headers) | `typecheck-and-lint`, `base-only` (wheel), publish workflows (wheel + sdist) |
| No dead parameters or stale suppressions | ruff `ARG`, `RUF100`, `TRY203` | `ruff check .` |
| Logging convention (#717) | ruff `BLE`, `LOG`, `G`; `tests/unit/test_logging.py` (DEBUG records for retry and 401 refresh; no header value in any record) | `ruff check .`, `pytest` |
| Public API surface (#719) | ruff `RUF022`, `SLF001`; `tests/unit/test_public_api_surface.py`; `tests/unit/test_architecture.py` (no cross-package private import, #729) | `ruff check .`, `pytest` |
| Docstrings on public symbols (#722) | ruff `D101`-`D103` on `src/`; `tests/unit/test_public_docstrings.py` (summary + docs link on the headline entry points) | `ruff check .`, `pytest` |
| Error contract (#715) | `tests/unit/test_error_taxonomy.py` (every `DonkeyError` exported from `donkey_kit` or `donkey_kit.experimental`, own non-empty overridable remediation; `classify` exported); "never a builtin exception" is review-only | `pytest` |
| One source of truth, no dead code (#720) | `tests/unit/test_wire_names.py` (no gateway header literal outside `core/_wire.py`); `vulture` with `vulture_whitelist.py`; deprecate-before-remove is review-only | `pytest`, `typecheck-and-lint`: `vulture` |

Self-review before pushing = the pre-PR gate in Section 1 (`mypy`, `ruff check .`,
`lint-imports`, `vulture`, `pytest`), plus `verify_frameworks.py` if you touched adapters.

### Logging

- **One logger per module that logs:** `_log = logging.getLogger(__name__)`.
  Never hard-code a logger name. The package root (`donkey_kit/__init__.py`)
  attaches a `NullHandler` to `"donkey_kit"` and nothing else: the application
  owns handlers, levels, and formatting.
- **DEBUG** for what the SDK does on the caller's behalf: each retry (status,
  attempt, delay), the 401 token refresh, fallbacks (a gateway routing fallback
  that is not retried, an auth provider falling through), and the OTLP export
  bootstrap.
- **WARNING** only for a condition the user can act on. A one-time
  configuration problem the user must fix is usually a `warnings.warn(...)`
  category instead (see `TelemetryExportWarning`).
- **Never log a header value**, a request body, or an exception message that
  could echo one. Log the method, host, and path (no query string), the status,
  and type names. `tests/unit/test_logging.py` asserts that no record carries a
  sent credential.
- **No silent swallowing.** An `except` that does not re-raise either logs
  `_log.debug(..., exc_info=True)` or says inline why the failure is expected.
  Ruff selects `BLE`, `LOG`, and `G`, so a blind `except Exception` needs a
  `# noqa: BLE001 — <reason>` that states the reason. Pass log arguments
  lazily (`_log.debug("%s", x)`), never as an f-string.

---

## 4. Docs-sync rule

`website/` (Nextra) describes how the SDK behaves from a consumer's
perspective. When code changes what the SDK does — or which platform facts it
depends on — the docs must change *with it*, or the drift is discovered by a
confused adopter instead of at review time. There is no automated drift detector;
this is a PR-time discipline. The surface→page map (code paths under
`python/src/donkey_kit/`, pages under `website/content/`):

| Code surface | Docs page(s) |
| --- | --- |
| `core/errors.py` | `errors.mdx` |
| `core/config.py`, `core/auth.py`, `core/endpoints.py` | `reference/configuration.mdx` |
| `core/_verify.py`, `docs/verified-apis.md`, `docs/unsupported-boundary.md` | `reference/unsupported-boundary.mdx`, and any page that states the changed status (`roadmap.mdx`, `frameworks/index.mdx`) |
| `core/budget.py` | `budget.mdx` |
| `core/telemetry.py`, `core/cost.py` | `telemetry.mdx` |
| `core/lastcall.py` | `reference/last-call.mdx` |
| `llm/*` | `quickstart.mdx`, `feature-overview.mdx` |
| `simulator/*` | `simulator.mdx` |
| `conformance/*`, `donkey.simulate()` | `testing.mdx` |
| `integrations/<fw>.py` | `frameworks/<fw>.mdx` + `examples/<fw>.mdx` (note `openai_agents.py` → `frameworks/openai.mdx`, `examples/openai-agents.mdx`); `frameworks/index.mdx` if the roster or an adapter's depth changes |
| `registry/criteria.py`, `registry/introspect.py`, `registry/models.py`, `tools/filter.py` | `tool-access/discovery.mdx` |
| `registry/publication.py`, `registry/exchange.py` | `publishing.mdx` |
| `tools/session.py` | `tool-access/binding.mdx` |
| `cli/*` (`init`, `doctor`, `mock`, `test`, the blocked `status`/`publish`/`verify`) | `cli.mdx` (the site has no provisioning section; don't add one, the control plane is refused, ADR 0008 in `docs/adr/`) |
| `experimental.py` | `reference/unsupported-boundary.mdx`, plus the page of the surface whose types moved (`publishing.mdx`, `tool-access/discovery.mdx`) |
| `README.md` (install/status/extras) | `quickstart.mdx`, `index.mdx` |

`docs/verified-apis.md` is not "engineering-internal" for this purpose: when
a row moves to **`VERIFIED (LIVE)`**, the same PR must remove **every**
remaining "unverified / documented-only / pending capture" claim about that
shape, so one surface never says "live" while another still says "planned" or
"pending." Sweep, in the same PR:

- `classify()` and the related docstrings in `python/src/donkey_kit/core/`
  (notably `core/errors.py`);
- `docs/unsupported-boundary.md`;
- `website/content/**` **and** the generated `website/public/**` copies
  (regenerate with `npm run generate:llms`);
- the fixture index (`python/src/donkey_kit/simulator/_fixtures/rejections/README.md`);
- the affected test module docstrings.

Do **not** touch the top-level `README.md` status banner for an individual
fixture/row flip — that banner tracks milestone/release-level posture, not a
single shape going live, and moves only when the overall status does.

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

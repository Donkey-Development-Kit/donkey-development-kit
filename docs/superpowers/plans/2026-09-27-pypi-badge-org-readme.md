# PyPI Version Badge on Org Homepage README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live, auto-updating PyPI-version badge to the
`Donkey-Development-Kit/.github` org homepage README and correct the stale
"not yet published to PyPI" banner, so the org profile page
(https://github.com/Donkey-Development-Kit) reflects that `donkey-kit` is
actually published.

**Architecture:** This is a single-file documentation change in a repository
that is **not** `donkey-development-kit` — the change lives in
`profile/README.md` of the sibling `Donkey-Development-Kit/.github` repo,
which has no local clone yet and no CI. There is exactly one task: clone the
`.github` repo as a workspace sibling, cut an issue branch from its `main`
(this repo has no `develop`), edit the README, verify by re-reading the
rendered file content and grepping for the banned stale strings, commit, push,
open a PR against `main`, and confirm the acceptance checklist.

**Tech Stack:** Plain Markdown + inline HTML (`<p align="center">`), a
shields.io dynamic badge (`img.shields.io/pypi/v/donkey-kit`), `gh` CLI for
issue/PR operations. No build, no tests, no CI in this repo.

**Spec:** GitHub issue
[Donkey-Development-Kit/donkey-development-kit#664](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/664)
("Add a PyPI version badge to the org homepage README") — filed in the SDK
repo per this org's convention, but the diff targets the separate
`Donkey-Development-Kit/.github` repo. Sibling issue
[#663](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/663)
(docs-site navbar badge, already implemented on branch
`docs/663-pypi-badge-navbar`, commit `e90eebb`) sets the shields.io query-param
convention this plan reuses for visual consistency:
`?logo=pypi&logoColor=white&label=PyPI`.

## Global Constraints

- Badge must be a **live shields.io badge** (`https://img.shields.io/pypi/v/donkey-kit`)
  linking to `https://pypi.org/project/donkey-kit/` — not a static image or a
  hardcoded version string (issue acceptance criteria).
- The "Get started" banner must **not reintroduce a hardcoded version number**
  in prose — the badge now carries the version; the banner text must stay
  version-agnostic (issue proposal, item 2).
- Must render correctly in GitHub's light and dark themes (issue acceptance
  criteria) — shields.io badges are theme-neutral bitmaps, but this still
  needs a visual check on the actual rendered page, not just a source-diff
  read.
- Change is scoped to `Donkey-Development-Kit/.github`'s `profile/README.md`
  only (issue "Out of scope" section) — do not touch
  `website/content/roadmap.mdx` (owned by #663's noted follow-up) or anything
  else in that repo.
- No local clone of `Donkey-Development-Kit/.github` exists yet under
  `~/ClaudeProjects/ddk/` — it must be cloned fresh as part of this task, per
  [[ddk-git-workflow]]'s "one issue = one branch" discipline, applied to this
  repo's own `main`-only branch model (it has no `develop`).
- This repo has **no CI** (`gh api .../contents/.github/workflows` → 404) —
  verification is manual: re-read the file content, grep for banned strings,
  and visually preview the rendered page.

## Review Focus

- **Stray stale-version references outside the one banner line.** The spec
  only calls out the "Get started" banner, but a careless edit could leave
  another mention of `v0.1.0.dev1` or "not yet published" elsewhere in the
  file (e.g. the repo table or footer) untouched. Task 1 Step 6 greps the
  *whole file*, not just the edited hunk.
- **Badge/link mismatch.** A reasonable visitor expects the badge image and
  its click-through link to point at the *same* thing — the shields.io image
  URL must read `donkey-kit` and the wrapping `<a href>` must be exactly
  `https://pypi.org/project/donkey-kit/`, not a lookalike (PyPI project slugs
  are case- and punctuation-sensitive). Task 1 Step 4 asserts both strings.
- **Visual inconsistency with the sibling badge (#663).** Nothing in the
  issue *requires* matching the navbar badge's styling, but a visitor who
  sees both surfaces would find two different-looking "PyPI" badges jarring.
  Task 1 Step 3 reuses the same shields.io query params
  (`?logo=pypi&logoColor=white&label=PyPI`) already shipped in #663.
- **Markdown/HTML breakage from the edit.** This README mixes raw HTML
  (`<p align="center">`) with Markdown; an unclosed tag or stray backtick can
  silently break rendering for everything *after* the edit point, not just
  the edited paragraph. Task 1 Step 7 previews the full rendered page, not
  just the diff.
- **Command block still implying a source install.** The issue explicitly
  flags the `git clone && pip install -e` block as the thing to replace with
  `pip install "donkey-kit[...]"` — a partial edit that swaps the banner
  sentence but leaves the old `git clone` code block behind would still tell
  visitors to build from source. Task 1 Step 5 removes the whole stale code
  block, not just the sentence above it.

---

### Task 1: Add the PyPI badge and fix the stale "Get started" banner

**Files:**
- Clone: `Donkey-Development-Kit/.github` to a new sibling directory
  `~/ClaudeProjects/ddk/dot-github-664-pypi-badge` (this repo doesn't exist
  locally yet; there is nothing to isolate from, so no worktree — a fresh
  clone on its own branch is the isolation).
- Modify: `profile/README.md` (in that clone).

**Interfaces:**
- Consumes: nothing from earlier tasks — this is the only task.
- Produces: nothing consumed by a later task — this is the only task.

- [ ] **Step 1: Find/assign the issue, clone the repo, cut the branch**

The issue already exists (#664, milestone "Phase 1.1 — Stabilize the MVP
(0.1.1)" is already set) but is currently unassigned. Assign it, then clone
`Donkey-Development-Kit/.github` as a new workspace sibling and cut a branch
from its `main` (this repo has no `develop` — confirmed via
`gh api repos/Donkey-Development-Kit/.github/branches --jq '.[].name'` →
only `main`):

```bash
gh issue edit 664 --repo Donkey-Development-Kit/donkey-development-kit --add-assignee @me

cd ~/ClaudeProjects/ddk
gh repo clone Donkey-Development-Kit/.github dot-github-664-pypi-badge
cd dot-github-664-pypi-badge
git checkout -b docs/664-pypi-badge-readme
```

- [ ] **Step 2: Capture the current README content for reference**

```bash
cat profile/README.md
```

Expected: a centered `<h1>`, two `<p align="center">` blocks (tagline, then
description), a "Repositories" table, a "Get started" section with the stale
`> **Alpha, pre-release** (\`v0.1.0.dev1\`) — not yet published to PyPI.
Install from source:` line followed by a ```` ```bash ```` block running
`git clone` + `cd` + `pip install -e`, then a closing paragraph and the
trademark-disclaimer footer.

- [ ] **Step 3: Add the badge paragraph**

Insert a new centered paragraph directly after the `<h1>` and before the
existing tagline paragraph, reusing the shields.io query params already
shipped in #663's navbar badge (`?logo=pypi&logoColor=white&label=PyPI`) for
visual consistency across the two surfaces:

Before:
```html
<h1 align="center">🫏 Donkey Development Kit</h1>

<p align="center">
  <strong>Takes the donkey work out of AI development.</strong>
</p>
```

After:
```html
<h1 align="center">🫏 Donkey Development Kit</h1>

<p align="center">
  <a href="https://pypi.org/project/donkey-kit/">
    <img src="https://img.shields.io/pypi/v/donkey-kit?logo=pypi&logoColor=white&label=PyPI" alt="PyPI version">
  </a>
</p>

<p align="center">
  <strong>Takes the donkey work out of AI development.</strong>
</p>
```

- [ ] **Step 4: Verify the badge markup landed correctly**

```bash
rg -F 'img.shields.io/pypi/v/donkey-kit' profile/README.md
rg -F 'href="https://pypi.org/project/donkey-kit/"' profile/README.md
```

Expected: both commands print exactly one matching line each, and the two
lines sit within three lines of each other in the file (the `<a>` wraps the
`<img>`).

- [ ] **Step 5: Replace the stale "Get started" banner and source-install block**

Before:
```markdown
## Get started

> **Alpha, pre-release** (`v0.1.0.dev1`) — not yet published to PyPI. Install from source:

```bash
git clone https://github.com/Donkey-Development-Kit/donkey-development-kit.git
cd donkey-development-kit/python
pip install -e ".[llm,langgraph]"   # base + raw client + one framework
```
```

After:
```markdown
## Get started

> **Alpha — published on PyPI.**

```bash
pip install "donkey-kit[llm,langgraph]"   # base + raw client + one framework
```
```

Leave the following paragraph ("Then head to the [SDK repo]...") and the
footer untouched — only the blockquote line and the fenced command block
change.

- [ ] **Step 6: Verify no stale strings remain anywhere in the file**

```bash
rg -F 'not yet published' profile/README.md; echo "exit:$?"
rg -F 'v0.1.0.dev1' profile/README.md; echo "exit:$?"
rg -F 'git clone https://github.com/Donkey-Development-Kit/donkey-development-kit.git' profile/README.md; echo "exit:$?"
```

Expected: all three `rg` calls find nothing, so each prints `exit:1` (ripgrep's
no-match exit code). If any prints `exit:0`, a stale reference survived —
go back to Step 5.

- [ ] **Step 7: Visually preview the rendered page**

Push the branch, open a draft PR (base `main`), and view the "Files changed"
diff render on GitHub — or open the raw README preview at
`https://github.com/Donkey-Development-Kit/.github/blob/docs/664-pypi-badge-readme/profile/README.md`
in a browser once pushed. Confirm:
- The new badge paragraph renders centered, directly under the title, above
  the tagline, in both GitHub light and dark theme (toggle via GitHub's theme
  switcher or OS-level dark mode).
- The badge image loads (shields.io reachable) and links out to
  `https://pypi.org/project/donkey-kit/`.
- The "Get started" section shows the new one-line `pip install` block with
  no leftover `git clone`/`cd` lines.
- The repo table, description paragraphs, and trademark-disclaimer footer are
  unchanged and still render correctly (nothing above/below the edit broke).

- [ ] **Step 8: Commit**

```bash
git add profile/README.md
git commit -m "$(cat <<'EOF'
docs: add PyPI version badge and fix stale install banner

Closes #664.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 9: Push and open the PR against `main`**

```bash
git push -u origin docs/664-pypi-badge-readme
gh pr create --repo Donkey-Development-Kit/.github \
  --base main \
  --title "docs: add PyPI version badge and fix stale install banner" \
  --body "$(cat <<'EOF'
## Summary
- Adds a live shields.io PyPI-version badge (linking to
  https://pypi.org/project/donkey-kit/) to the org homepage header, matching
  the styling already shipped for the docs-site navbar in #663.
- Replaces the stale "Alpha, pre-release ... not yet published to PyPI"
  banner and its `git clone && pip install -e` block with a version-agnostic
  "published on PyPI" note and a `pip install "donkey-kit[...]"` command.

Closes #664.

## Test plan
- [ ] Badge renders and links to https://pypi.org/project/donkey-kit/ (checked
  in GitHub's PR file-diff preview).
- [ ] Get-started section no longer claims "not yet published to PyPI" and
  shows a `pip install` path, with no hardcoded version number.
- [ ] Renders correctly in GitHub light and dark themes.
- [ ] Rest of the README (repo table, footer) unchanged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 10: Confirm every issue acceptance-criteria box**

Re-read issue #664's acceptance criteria and check each one against the
pushed branch/PR:
- [ ] Org homepage shows a PyPI-version badge linking to
  `https://pypi.org/project/donkey-kit/`.
- [ ] Badge is dynamic (shields.io), not a hardcoded string.
- [ ] "Get started" no longer claims "not yet published to PyPI"; shows
  `pip install donkey-kit[...]`.
- [ ] No hardcoded version number reintroduced in README prose.
- [ ] Renders correctly in GitHub light and dark themes.

---

## Self-Review

**Spec coverage:** Every bullet in issue #664's Proposal and Acceptance
Criteria maps to a step above — badge (Steps 3–4, 7), banner text (Step 5),
version-agnostic constraint (Step 6, Global Constraints), light/dark render
check (Step 7, Step 10), scope limited to this one file in this one repo
(Task 1's Files section, Global Constraints).

**Placeholder scan:** No "TBD"/"add appropriate handling"/"similar to Task
N" — this is a one-task plan and every step shows the literal before/after
text or command.

**Type consistency:** N/A — no functions/interfaces span steps; this is a
single Markdown file edit with no cross-step signatures to keep aligned.

**Review Focus:** Five risks identified above (stray stale references,
badge/link mismatch, visual inconsistency with #663, markdown breakage,
partial banner edit) each have an owning verification step (Steps 4, 6, 7,
and the full-block replacement in Step 5) rather than being left to the
"Global Constraints" prose alone.

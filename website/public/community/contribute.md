# Contribute

DDK is open source under the Apache-2.0 licence, and contributions of every
size are welcome. This page is the short version; the full runbook is
[`CONTRIBUTING.md`](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/develop/CONTRIBUTING.md)
in the repository.

## Ways to help

  
    Every change starts as an issue. Search first, then file one with what you
    expected and what happened.
  
  
    Small, well-scoped issues that are a good way into the codebase.
  
  
    This site lives in `website/` (Nextra). Every page has an "Edit this page"
    link.
  
  
    Runnable demos live in the companion `donkey-development-kit-demos`
    repository.
  

## From issue to merge

### File or find the issue

No change lands without a GitHub issue — the issue is the plan. Each issue
carries exactly one milestone, which is the release it targets (see the
[Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md)).

### Cut a branch from `develop`

Branch names follow `<type>/<issue#>-<slug>`, for example
`fix/42-proxy-url-trailing-slash` or `docs/13-verified-apis-update`. Always
branch from `develop`; never from or into `main`.

```bash
git checkout develop && git pull --ff-only
git checkout -b docs/13-verified-apis-update
```

### Run the pre-PR gate

Run the same checks CI runs, from `python/`:

```bash
pip install -e ".[dev,llm,cli]"
pytest -q          # tests
mypy               # mypy --strict
ruff check .       # lint
lint-imports       # the framework-free core contract
```

If you touched an adapter, also run `python scripts/verify_frameworks.py`.

### Open a pull request into `develop`

The PR body includes `Closes #<issue#>`, a `## Summary`, a `## Test plan` and
a `## Post-deploy steps` section (write `None.` when nothing applies). PRs are
squash-merged, so `develop` reads as one commit per issue.

## Contributing from a fork

Not a member of the `Donkey-Development-Kit` organisation? The flow is the
same, from a fork:

```bash
git clone https://github.com/<you>/donkey-development-kit.git
cd donkey-development-kit
git remote add upstream https://github.com/Donkey-Development-Kit/donkey-development-kit.git
git fetch upstream
git checkout -b docs/13-verified-apis-update upstream/develop
```

Open the PR from your fork into `Donkey-Development-Kit:develop` and tick
**Allow edits by maintainers**. A maintainer sets the milestone and labels,
runs the secret-gated checks that GitHub does not run on fork PRs, and merges.

## Keep the docs in sync

When code changes what DDK does, the docs change in the same pull request —
or a `documentation` follow-up issue is filed and linked. After editing pages
here, regenerate the AI-readable docs and commit the result:

```bash
cd website
npm run generate:llms
```

  **Verification discipline.** DDK never documents or codes against an
  endpoint, header or class name that has not been confirmed against the real
  platform. If you can't confirm one, say so in the issue rather than guessing
  — see [Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md).

"""Remind a PR that changes an SDK surface to update its mapped docs page (#797).

``CONTRIBUTING.md`` §4 (the docs-sync rule) maps each code surface under
``python/src/donkey_kit/`` (plus the verification ledger and the root
``README.md``) to the ``website/content/`` page(s) that describe it. This
script is that map as data. Given the files a PR changed, it lists
every surface whose code changed while none of its mapped pages did: the PR
then either updates the page or links a ``documentation`` follow-up issue.

It is a reminder, not a gate: a refactor can change a surface without changing
what the page says. By default it prints the reminders (as GitHub
``::warning`` annotations under Actions) and exits 0; ``--strict`` exits 1 when
there is any.

    python scripts/check_docs_sync.py --base origin/develop [--head HEAD]
    git diff --name-only A...B | python scripts/check_docs_sync.py --files -
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[2]

_SRC = "python/src/donkey_kit/"
_PAGES = "website/content/"


@dataclass(frozen=True)
class Surface:
    """A set of code paths and the docs pages that must move with them.

    ``code`` patterns are relative to ``python/src/donkey_kit/``; ``files`` are
    repo-root paths outside the package (the ledger, the root README).
    """

    code: tuple[str, ...]
    pages: tuple[str, ...]
    files: tuple[str, ...] = ()

    def matches(self, path: str) -> bool:
        return path in self.files or any(
            fnmatch.fnmatchcase(path, _SRC + pattern) for pattern in self.code
        )

    @property
    def page_paths(self) -> tuple[str, ...]:
        return tuple(_PAGES + page for page in self.pages)


def _framework(module: str, *pages: str) -> Surface:
    return Surface((f"integrations/{module}.py",), pages)


#: CONTRIBUTING.md §4's surface -> page map, every row of it. Code patterns are
#: relative to python/src/donkey_kit/, ``files`` to the repo root, pages to
#: website/content/; any one listed page changing satisfies the surface. Keep
#: the two in step, and keep the workflow's ``paths:`` covering ``files``.
SURFACES: tuple[Surface, ...] = (
    Surface(("core/errors.py",), ("errors.mdx",)),
    Surface(
        ("core/config.py", "core/auth.py", "core/endpoints.py"),
        ("reference/configuration.mdx",),
    ),
    Surface(
        ("core/_verify.py",),
        ("reference/unsupported-boundary.mdx", "roadmap.mdx", "frameworks/index.mdx"),
        files=("docs/verified-apis.md", "docs/unsupported-boundary.md"),
    ),
    Surface(("core/budget.py",), ("budget.mdx",)),
    Surface(("core/telemetry.py", "core/cost.py"), ("telemetry.mdx",)),
    Surface(("core/lastcall.py",), ("reference/last-call.mdx",)),
    Surface(("llm/*.py",), ("quickstart.mdx", "feature-overview.mdx")),
    Surface(("simulator/*.py",), ("simulator.mdx",)),
    Surface(("conformance/*.py",), ("testing.mdx",)),
    Surface(("donkey.py",), ("quickstart.mdx", "testing.mdx", "reference/configuration.mdx")),
    Surface(("integrations/__init__.py", "integrations/_base.py"), ("frameworks/index.mdx",)),
    _framework("adk", "frameworks/adk.mdx", "examples/adk.mdx"),
    _framework("agent_framework", "frameworks/agent-framework.mdx", "examples/agent-framework.mdx"),
    _framework("anthropic", "frameworks/anthropic.mdx", "examples/anthropic.mdx"),
    _framework("crewai", "frameworks/crewai.mdx", "examples/crewai.mdx"),
    _framework("langgraph", "frameworks/langgraph.mdx", "examples/langgraph.mdx"),
    _framework("llamaindex", "frameworks/llamaindex.mdx", "examples/llamaindex.mdx"),
    _framework("openai_agents", "frameworks/openai.mdx", "examples/openai-agents.mdx"),
    _framework("strands", "frameworks/strands.mdx", "examples/strands.mdx"),
    Surface(
        (
            "registry/criteria.py",
            "registry/introspect.py",
            "registry/models.py",
            "tools/filter.py",
        ),
        ("tool-access/discovery.mdx",),
    ),
    Surface(("registry/publication.py", "registry/exchange.py"), ("publishing.mdx",)),
    Surface(("tools/session.py",), ("tool-access/binding.mdx",)),
    Surface(("cli/*.py",), ("cli.mdx",)),
    Surface(("experimental.py",), ("reference/unsupported-boundary.mdx",)),
    Surface((), ("quickstart.mdx", "index.mdx"), files=("README.md",)),
)


@dataclass(frozen=True)
class Reminder:
    """Code in ``changed`` moved without any of ``pages``."""

    changed: tuple[str, ...]
    pages: tuple[str, ...]

    def __str__(self) -> str:
        pages = " or ".join(self.pages)
        return f"{', '.join(self.changed)} changed without {pages}"


def reminders(changed: list[str]) -> list[Reminder]:
    """The surfaces ``changed`` touches whose mapped pages it leaves untouched."""
    touched = set(changed)
    out: list[Reminder] = []
    for surface in SURFACES:
        code = tuple(sorted(path for path in touched if surface.matches(path)))
        if code and not touched.intersection(surface.page_paths):
            out.append(Reminder(code, surface.page_paths))
    return out


def changed_files(root: Path, base: str, head: str) -> list[str]:
    """``git diff --name-only base...head``: what the PR changed since it branched."""
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _report(found: list[Reminder]) -> None:
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    for reminder in found:
        if annotate:
            print(
                f"::warning file={reminder.changed[0]},title=docs-sync::{reminder}; "
                "update the page or link a documentation follow-up (CONTRIBUTING.md §4)"
            )
        else:
            print(f"docs-sync: {reminder}", file=sys.stderr)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and found:
        lines = [
            "## Docs-sync reminder",
            "",
            "These SDK surfaces changed without their mapped docs page "
            "(CONTRIBUTING.md §4). Update the page in this PR, or link a "
            "`documentation` follow-up issue:",
            "",
            *(
                f"- `{', '.join(r.changed)}` → {' or '.join(f'`{p}`' for p in r.pages)}"
                for r in found
            ),
            "",
        ]
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=_DEFAULT_ROOT)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--base", help="git ref the PR is merged into")
    source.add_argument("--files", help="file listing changed paths, one per line; - for stdin")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--strict", action="store_true", help="exit 1 on any reminder")
    args = parser.parse_args(argv)

    if args.base:
        changed = changed_files(args.root, args.base, args.head)
    elif args.files == "-":
        changed = [line.strip() for line in sys.stdin if line.strip()]
    else:
        text = Path(args.files).read_text(encoding="utf-8")
        changed = [line.strip() for line in text.splitlines() if line.strip()]

    found = reminders(changed)
    _report(found)
    if not found:
        print("docs-sync: every changed surface has its mapped page in the diff.")
    return 1 if found and args.strict else 0


if __name__ == "__main__":
    sys.exit(main())

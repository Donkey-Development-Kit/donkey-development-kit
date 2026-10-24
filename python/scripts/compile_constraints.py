"""Refresh the pip constraints files PR CI resolves from (ADR 0007 rule 3, #948).

PR CI must resolve from a committed lock, so a PR's result does not change when
an upstream package releases between the branch being cut and CI running
(docs/adr/0007-dependency-policy.md, rule 3). This script is that lock's
refresh command: it drives `uv pip compile` (dev-time only; CI itself still
installs with plain `pip install -c <constraints>`, per ADR 0007) once per
exact `(extras, dev-group, Python version)` combination a PR-gating job in
`.github/workflows/ci.yml` installs, and writes each compile straight to its
own file under `python/constraints/`.

One file per (combo, python version) — NEVER merged. An earlier version of
this script merged every combo's pins into one file per Python version,
reasoning that pip constraints only restrict a package a job actually
requests, so an unused pin is harmless. That reasoning is wrong when two
combos need the SAME package at genuinely incompatible versions: merging
took the higher version, and `openai-agents`, `google-adk`, `crewai`, and
`llama-index-llms-openai(-like)` all pin ceilings on dependencies (`websockets`,
`regex`, `openai`) that a *different* combo's own compile pushed past. A
merged constraints file does not make the install satisfiable for the combo
whose ceiling it violates — it makes it fail (confirmed: `uv pip compile`
against the old merged file raised "No solution found" for
`[openai-agents,strands]`, `[llamaindex]`, `[adk]`, and `[crewai]`). Compiling
each combo on its own and writing its own file sidesteps the whole problem:
each file is exactly the resolution `uv` found for exactly that combo, so it
is satisfiable for that combo by construction. The price is more files
(one per combo per Python version it actually runs on, not all three) and a
little duplication (two combos with identical extras get two files with
identical content) — both fine for a generated, never-hand-edited lock.

The *jobs* this script's output must stay in sync with are listed as
`_COMBOS` below, each tagged with the `ci.yml` job(s) it covers and the exact
`extras`/`dev_group` its `pip install -e ".[...]" --group dev` line uses — this
must match the job's install step byte for byte, or the file compiled here
is not the file CI needs. A job that ADR 0007 rules 1-2 require to resolve
fresh (`all-extra-resolves`, `anthropic-stacks`, `adk-stacks`) and the nightly
matrix are deliberately NOT compiled here and must never gain a
`-c constraints/...` flag.

Usage (from `python/`, needs `uv` on PATH — a dev-time tool only; see
CONTRIBUTING.md):

    python scripts/compile_constraints.py          # refresh constraints/*.txt
    python scripts/compile_constraints.py --check   # exit 1 if refreshing would change a file

The repository's `.pre-commit-config.yaml` restates two pins of the
typecheck-and-lint lock: the ruff-pre-commit `rev:` and the lint-imports hook's
`import-linter==` dependency (`tests/unit/test_noxfile.py` holds them equal).
Every refresh rewrites those two from the lock it just compiled, so a ruff or
import-linter release reaches the hooks in the same bump PR (#1206).

`--check` recompiles every combo and diffs against the committed file. Because
each combo is compiled on its own (never merged), a successful `--check` run
also proves every committed file is still satisfiable for its own combo: an
unsatisfiable combo makes `uv pip compile` fail outright (a hard error, not a
diff), not produce a file this script could silently accept.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parents[1]
_CONSTRAINTS_DIR = _PYTHON_ROOT / "constraints"
_PRE_COMMIT = _PYTHON_ROOT.parent / ".pre-commit-config.yaml"
#: The typecheck-and-lint job's lock: the ruff and import-linter the hooks run.
_LINT_LOCK = _CONSTRAINTS_DIR / "typecheck-and-lint-py3.11.txt"
#: The ruff-pre-commit repo's `rev:` line (its tags are `v<ruff version>`).
_RUFF_REV = re.compile(
    r"^(\s*- repo: https://github\.com/astral-sh/ruff-pre-commit\n\s+rev: )v\S+$",
    re.MULTILINE,
)
#: The lint-imports hook's pinned dependency.
_IMPORT_LINTER_DEP = re.compile(r"(?<![\w-])(import-linter==)[^\s,\]]+")


@dataclass(frozen=True)
class Combo:
    """One exact install line a workflow job runs: `pip install -e ".[...]" [--group dev]`
    for a PR-gating ci.yml job, or the tool-only install of a dependency group."""

    name: str
    extras: tuple[str, ...]
    dev_group: bool
    python_versions: tuple[str, ...]
    covers: str  # which workflow job(s), for the comment header
    #: Extra requirement specifiers the job's install line adds after the
    #: extras, e.g. ``("openai<2",)`` for the `openai-1x` leg (#769).
    requirements: tuple[str, ...] = ()
    # Further dependency groups, each passed as `--group <name>` (#763).
    groups: tuple[str, ...] = ()
    # False for a tool-only lock (the `release` and `lock` groups): the compile
    # then covers just the groups, not donkey-kit's own dependencies, because
    # the job installs only those tools (`pip install -c <file> build twine`).
    project: bool = True

    def file_name(self, python_version: str) -> str:
        return f"{self.name}-py{python_version}.txt"


# Every PR-gating job in .github/workflows/ci.yml that installs from this
# repo's own extras/dev group, one Combo per distinct install line. `extras`
# and `dev_group` must match that job's `pip install -e ".[...]" [--group dev]`
# step EXACTLY — not a superset "to be safe": a superset can make `uv pip
# compile` select versions the job's own, narrower install would never have
# requested, which is exactly how the old merged-file design broke (see the
# module docstring).
#
# The last two combos lock tools rather than a ci.yml install line (#763): the
# release toolchain (build, twine, hatchling) and the lock's own `uv`.
#
# Deliberately NOT covered (ADR 0007 rules 1-2, resolve fresh every run):
#   all-extra-resolves, anthropic-stacks, adk-stacks, and everything in
#   nightly-matrix.yml.
_COMBOS: tuple[Combo, ...] = (
    Combo("base-only", (), True, ("3.11",), "base-only"),
    Combo("typecheck-and-lint", ("llm", "cli"), True, ("3.11",), "typecheck-and-lint"),
    Combo(
        "test",
        ("llm", "cli", "local", "otel"),
        True,
        ("3.10", "3.11", "3.12", "3.13", "3.14"),
        "test (matrix)",
    ),
    # The openai 1.x leg (#769): the `llm` extra's floor is openai>=1.66 and
    # every other combo resolves the 2.x line, so this one adds `"openai<2"`
    # to its install line to keep the 1.x range tested.
    Combo(
        "openai-1x",
        ("llm", "cli", "local", "otel"),
        True,
        ("3.12",),
        "openai-1x",
        requirements=("openai<2",),
    ),
    Combo("benchmark", ("llm", "cli", "local", "otel"), True, ("3.11",), "benchmark"),
    Combo("quickstart", ("llm", "local", "otel"), False, ("3.11",), "quickstart"),
    Combo(
        "langgraph-demo",
        ("llm", "langgraph", "local", "otel"),
        True,
        ("3.11",),
        "langgraph-demo",
    ),
    Combo(
        "openai-agents-demo",
        ("llm", "openai-agents", "local", "otel"),
        True,
        ("3.11",),
        "openai-agents-demo",
    ),
    Combo(
        "agent-framework-middleware",
        ("llm", "agent_framework"),
        True,
        ("3.12",),
        "agent-framework-middleware",
    ),
    Combo(
        "llamaindex-transport",
        ("llm", "llamaindex"),
        True,
        ("3.12",),
        "llamaindex-transport",
    ),
    Combo(
        "agents-strands-last-call",
        ("llm", "openai-agents", "strands"),
        True,
        ("3.12",),
        "agents-strands-last-call",
    ),
    # One combo per adapter-contract matrix leg: that job installs
    # `.[llm,local,$DONKEY_CONTRACT_EXTRA]` — no `cli`, no `otel` — so each
    # leg's own compile must use exactly that, not the base combo's extras.
    Combo(
        "adapter-contract-langgraph",
        ("llm", "local", "langgraph"),
        True,
        ("3.12",),
        "adapter-contract(langgraph)",
    ),
    Combo(
        "adapter-contract-adk",
        ("llm", "local", "adk"),
        True,
        ("3.12",),
        "adapter-contract(adk)",
    ),
    Combo(
        "adapter-contract-strands",
        ("llm", "local", "strands"),
        True,
        ("3.12",),
        "adapter-contract(strands)",
    ),
    Combo(
        "adapter-contract-agent_framework",
        ("llm", "local", "agent_framework"),
        True,
        ("3.12",),
        "adapter-contract(agent_framework)",
    ),
    Combo(
        "adapter-contract-openai-agents",
        ("llm", "local", "openai-agents"),
        True,
        ("3.12",),
        "adapter-contract(openai-agents)",
    ),
    Combo(
        "adapter-contract-anthropic",
        ("llm", "local", "anthropic"),
        True,
        ("3.12",),
        "adapter-contract(anthropic)",
    ),
    Combo(
        "adapter-contract-crewai",
        ("llm", "local", "crewai"),
        True,
        ("3.12",),
        "adapter-contract(crewai)",
    ),
    Combo(
        "adapter-contract-llamaindex",
        ("llm", "local", "llamaindex"),
        True,
        ("3.12",),
        "adapter-contract(llamaindex)",
    ),
    # Tool-only locks (#763): no extras, no dev group, no donkey-kit deps.
    Combo(
        "release",
        (),
        False,
        ("3.11",),
        "publish-pypi.yml + publish-testpypi.yml build, base-only wheel build",
        groups=("release",),
        project=False,
    ),
    Combo(
        "lock",
        (),
        False,
        ("3.11",),
        "lock-refresh.yml",
        groups=("lock",),
        project=False,
    ),
)


def _compile_one(combo: Combo, python_version: str, out_file: Path) -> str:
    """Compile exactly ``combo``'s install line for ``python_version``, write ``out_file``.

    Returns the rendered content (header + the compiled pins), so the caller
    can either write it or diff it against what's committed.
    """
    cmd = ["uv", "pip", "compile"]
    if combo.project:
        cmd.append(str(_PYTHON_ROOT / "pyproject.toml"))
    cmd += [
        "--python-version",
        python_version,
        "--python-platform",
        "linux",  # ci.yml and nightly-matrix.yml both run on ubuntu-latest
        "--no-annotate",
        "--no-header",  # uv's own header embeds the scratch dir's absolute path
        "-o",
        str(out_file),
    ]
    for extra in combo.extras:
        cmd.extend(["--extra", extra])
    if combo.dev_group:
        cmd.extend(["--group", "dev"])
    if combo.requirements:
        # uv takes extra requirements as another input file, after pyproject.
        extra_in = out_file.with_suffix(".in")
        extra_in.write_text("\n".join(combo.requirements) + "\n", encoding="utf-8")
        cmd.insert(4 if combo.project else 3, str(extra_in))
    for group in combo.groups:
        # Without a SRC_FILE, uv reads the group from ./pyproject.toml (cwd below).
        cmd.extend(["--group", group])
    try:
        subprocess.run(cmd, check=True, cwd=_PYTHON_ROOT, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        print(exc.stderr, file=sys.stderr)
        raise
    compiled = out_file.read_text(encoding="utf-8")
    header = (
        "# GENERATED FILE — do not edit by hand.\n"
        f"# Refresh with: python scripts/compile_constraints.py  "
        f"(combo={combo.name!r}, python {python_version})\n"
        # Tool-only locks serve jobs outside ci.yml too (publish-*.yml, lock-refresh.yml).
        f"# Covers {'ci.yml' if combo.project else 'workflow'} job(s): "
        f"{combo.covers}\n"
        "# See scripts/compile_constraints.py and docs/adr/0007-dependency-policy.md rule 3.\n"
        "#\n"
    )
    return header + compiled


class PreCommitSyncError(RuntimeError):
    """The lint lock or `.pre-commit-config.yaml` lacks a pin the sync rewrites."""


def _lock_pin(lock_text: str, package: str) -> str:
    for line in lock_text.splitlines():
        if line.startswith(f"{package}=="):
            return line.split("==", 1)[1].strip()
    raise PreCommitSyncError(f"{package} is not pinned in {_LINT_LOCK.name}")


def sync_pre_commit_pins(config_text: str, lint_lock_text: str) -> str:
    """Return ``config_text`` with its ruff and import-linter pins taken from the lock.

    Rewrites the ruff-pre-commit ``rev:`` to ``v<ruff pin>`` and the
    lint-imports hook's ``import-linter==`` dependency to the import-linter
    pin, and leaves every other byte alone (a YAML round trip would drop the
    comments). Raises :class:`PreCommitSyncError` when either pin or either
    line is missing, so a renamed hook fails the refresh instead of leaving the
    hooks behind the lock.
    """
    ruff = _lock_pin(lint_lock_text, "ruff")
    import_linter = _lock_pin(lint_lock_text, "import-linter")
    synced, revs = _RUFF_REV.subn(lambda m: f"{m.group(1)}v{ruff}", config_text)
    synced, deps = _IMPORT_LINTER_DEP.subn(lambda m: f"{m.group(1)}{import_linter}", synced)
    if revs != 1:
        raise PreCommitSyncError(
            f"expected one astral-sh/ruff-pre-commit `rev:` in {_PRE_COMMIT.name}, found {revs}"
        )
    if deps != 1:
        raise PreCommitSyncError(
            f"expected one import-linter== pin in {_PRE_COMMIT.name}, found {deps}"
        )
    return synced


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if refreshing would change any committed constraints/*.txt",
    )
    args = parser.parse_args(argv)

    _CONSTRAINTS_DIR.mkdir(exist_ok=True)
    changed: list[str] = []
    lint_lock_text = ""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for combo in _COMBOS:
            for python_version in combo.python_versions:
                target = _CONSTRAINTS_DIR / combo.file_name(python_version)
                # Always compile to a scratch file, never straight to the
                # committed path — a --check run must not mutate it.
                scratch = tmp_dir / combo.file_name(python_version)
                content = _compile_one(combo, python_version, scratch)
                if target == _LINT_LOCK:
                    lint_lock_text = content
                if args.check:
                    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
                    if existing != content:
                        changed.append(target.name)
                else:
                    target.write_text(content, encoding="utf-8")
                    pins = sum(
                        1
                        for line in content.splitlines()
                        if line.strip() and not line.strip().startswith("#")
                    )
                    print(f"wrote {target.relative_to(_PYTHON_ROOT)} ({pins} pins)")

    # The hooks follow the lint lock just compiled (written, or only diffed under --check).
    config = _PRE_COMMIT.read_text(encoding="utf-8")
    synced = sync_pre_commit_pins(config, lint_lock_text)
    if synced != config:
        if args.check:
            changed.append(_PRE_COMMIT.name)
        else:
            _PRE_COMMIT.write_text(synced, encoding="utf-8")
            print(f"wrote {_PRE_COMMIT.name} (ruff and import-linter pins from {_LINT_LOCK.name})")

    if args.check and changed:
        names = ", ".join(changed)
        print(f"::error::stale lock files, run scripts/compile_constraints.py: {names}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

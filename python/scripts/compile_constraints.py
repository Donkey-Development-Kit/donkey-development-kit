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

`--check` recompiles every combo and diffs against the committed file. Because
each combo is compiled on its own (never merged), a successful `--check` run
also proves every committed file is still satisfiable for its own combo: an
unsatisfiable combo makes `uv pip compile` fail outright (a hard error, not a
diff), not produce a file this script could silently accept.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parents[1]
_CONSTRAINTS_DIR = _PYTHON_ROOT / "constraints"


@dataclass(frozen=True)
class Combo:
    """One exact install line a workflow job runs: `pip install -e ".[...]" [--group dev]`
    for a PR-gating ci.yml job, or the tool-only install of a dependency group."""

    name: str
    extras: tuple[str, ...]
    dev_group: bool
    python_versions: tuple[str, ...]
    covers: str  # which workflow job(s), for the comment header
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
        ("3.10", "3.11", "3.12"),
        "test (matrix)",
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
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for combo in _COMBOS:
            for python_version in combo.python_versions:
                target = _CONSTRAINTS_DIR / combo.file_name(python_version)
                # Always compile to a scratch file, never straight to the
                # committed path — a --check run must not mutate it.
                scratch = tmp_dir / combo.file_name(python_version)
                content = _compile_one(combo, python_version, scratch)
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

    if args.check and changed:
        names = ", ".join(changed)
        print(f"::error::stale constraints files, run scripts/compile_constraints.py: {names}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

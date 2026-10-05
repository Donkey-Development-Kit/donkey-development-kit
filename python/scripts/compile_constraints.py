"""Refresh the pip constraints files PR CI resolves from (ADR 0007 rule 3, #948).

PR CI must resolve from a committed lock, so a PR's result does not change when
an upstream package releases between the branch being cut and CI running
(docs/adr/0007-dependency-policy.md, rule 3). This script is that lock's
refresh command: it drives `uv pip compile` (dev-time only; CI itself still
installs with plain `pip install -c <constraints>`, per ADR 0007) once per
`(Python version, extras combo)` pair actually installed by a PR-gating CI job
in `.github/workflows/ci.yml`, then merges the results into one constraints
file per Python version under `python/constraints/`.

Why one merged file per Python version, not one per job: pip constraints only
restrict the version of a package a job actually asks to install — an unused
pin in the file is simply ignored. So the eight (soon ten) distinct extras
combinations across `ci.yml`'s PR-gating jobs can be merged into a single
`constraints/py3.NN.txt`; a job's `pip install -e ".[...]" --group dev -c
constraints/py3.NN.txt` only ever reads the pins for what it installed. Why
not one file total: the Python matrix is 3.10/3.11/3.12 and some pins (e.g.
`typing-extensions`, anything with a `python_version` marker upstream)
legitimately differ by interpreter version, so each gets its own file.

The *jobs* this script's output must stay in sync with are listed as
`_COMBOS` below, each tagged with the `ci.yml` job(s) it covers. A job that
ADR 0007 rules 1-2 require to resolve fresh (`all-extra-resolves`,
`anthropic-stacks`, `adk-stacks`) and the nightly matrix are deliberately
NOT compiled here and must never gain a `-c constraints/...` flag.

Usage (from `python/`, needs `uv` on PATH — a dev-time tool only; see
CONTRIBUTING.md):

    python scripts/compile_constraints.py          # refresh constraints/py3.*.txt
    python scripts/compile_constraints.py --check   # exit 1 if refreshing would change a file

Conflicting pins across combos (the same package resolved to two different
versions for two different frameworks) are resolved by taking the higher
version — consistent with "floors, never ceilings" (§8.4): a merged
constraints file should never hold a framework back from the version its own
compile selected.
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
_PYTHON_VERSIONS = ["3.10", "3.11", "3.12"]  # the ci.yml / nightly-matrix.yml matrix

_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)")


@dataclass(frozen=True)
class Combo:
    """One `(extras, dev group?)` combination a PR-gating ci.yml job installs."""

    name: str
    extras: tuple[str, ...]
    dev_group: bool
    covers: str  # which ci.yml job(s), for the comment header


# Every PR-gating job in .github/workflows/ci.yml that installs from this
# repo's own extras/dev group, grouped by distinct (extras, dev-group) pair.
# Keep this in sync with ci.yml: a job added there with a new combo needs a
# line here, or its `-c constraints/...` flag will pin against a stale set.
#
# Deliberately NOT covered (ADR 0007 rules 1-2, resolve fresh every run):
#   all-extra-resolves, anthropic-stacks, adk-stacks, and everything in
#   nightly-matrix.yml.
_COMBOS: tuple[Combo, ...] = (
    Combo(
        "base",
        ("llm", "cli", "local", "otel"),
        True,
        "base-only, typecheck-and-lint, test, benchmark, quickstart",
    ),
    Combo(
        "langgraph",
        ("llm", "cli", "local", "otel", "langgraph"),
        True,
        "langgraph-demo, adapter-contract(langgraph)",
    ),
    Combo("adk", ("llm", "cli", "local", "otel", "adk"), True, "adapter-contract(adk)"),
    Combo("strands", ("llm", "cli", "local", "otel", "strands"), True, "adapter-contract(strands)"),
    Combo(
        "agent_framework",
        ("llm", "cli", "local", "otel", "agent_framework"),
        True,
        "adapter-contract(agent_framework), agent-framework-middleware",
    ),
    Combo(
        "openai-agents",
        ("llm", "cli", "local", "otel", "openai-agents"),
        True,
        "adapter-contract(openai-agents)",
    ),
    Combo(
        "anthropic",
        ("llm", "cli", "local", "otel", "anthropic"),
        True,
        "adapter-contract(anthropic)",
    ),
    Combo("crewai", ("llm", "cli", "local", "otel", "crewai"), True, "adapter-contract(crewai)"),
    Combo(
        "llamaindex",
        ("llm", "cli", "local", "otel", "llamaindex"),
        True,
        "adapter-contract(llamaindex), llamaindex-transport",
    ),
    Combo(
        "agents-strands",
        ("llm", "cli", "local", "otel", "openai-agents", "strands"),
        True,
        "agents-strands-last-call",
    ),
)


def _compile_one(combo: Combo, python_version: str, out_dir: Path) -> dict[str, str]:
    """``{name: version}`` for one combo, via ``uv pip compile`` (dev-time tool only)."""
    out_file = out_dir / f"{combo.name}-py{python_version}.txt"
    cmd = [
        "uv",
        "pip",
        "compile",
        str(_PYTHON_ROOT / "pyproject.toml"),
        "--python-version",
        python_version,
        "--python-platform",
        "linux",  # ci.yml and nightly-matrix.yml both run on ubuntu-latest
        "--no-annotate",
        "--no-header",
        "-o",
        str(out_file),
    ]
    for extra in combo.extras:
        cmd.extend(["--extra", extra])
    if combo.dev_group:
        cmd.extend(["--group", "dev"])
    try:
        subprocess.run(cmd, check=True, cwd=_PYTHON_ROOT, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        print(exc.stderr, file=sys.stderr)
        raise
    pins: dict[str, str] = {}
    for line in out_file.read_text(encoding="utf-8").splitlines():
        match = _PIN_RE.match(line.strip())
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def _max_version(a: str, b: str) -> str:
    """The higher of two PEP 440-ish version strings (falls back to string order)."""
    try:
        from packaging.version import Version

        return a if Version(a) >= Version(b) else b
    except ImportError:  # pragma: no cover - packaging always available via uv's venv
        return max(a, b)


def merged_pins_for(python_version: str, out_dir: Path) -> dict[str, str]:
    """The merged ``{name: version}`` map across every combo, for one Python version."""
    merged: dict[str, str] = {}
    for combo in _COMBOS:
        pins = _compile_one(combo, python_version, out_dir)
        for name, version in pins.items():
            if name in merged and merged[name] != version:
                merged[name] = _max_version(merged[name], version)
            else:
                merged[name] = version
    return merged


def render(python_version: str, pins: dict[str, str]) -> str:
    """The committed ``constraints/py3.NN.txt`` content for one Python version."""
    lines = [
        "# GENERATED FILE — do not edit by hand.",
        f"# Refresh with: python scripts/compile_constraints.py  (python {python_version})",
        "# See scripts/compile_constraints.py and docs/adr/0007-dependency-policy.md rule 3.",
        "",
    ]
    for name in sorted(pins):
        lines.append(f"{name}=={pins[name]}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if refreshing would change any committed constraints/py3.*.txt",
    )
    args = parser.parse_args(argv)

    _CONSTRAINTS_DIR.mkdir(exist_ok=True)
    changed: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for python_version in _PYTHON_VERSIONS:
            pins = merged_pins_for(python_version, tmp_dir)
            content = render(python_version, pins)
            target = _CONSTRAINTS_DIR / f"py{python_version}.txt"
            if args.check:
                existing = target.read_text(encoding="utf-8") if target.is_file() else ""
                if existing != content:
                    changed.append(target.name)
            else:
                target.write_text(content, encoding="utf-8")
                print(f"wrote {target.relative_to(_PYTHON_ROOT)} ({len(pins)} pins)")

    if args.check and changed:
        names = ", ".join(changed)
        print(f"::error::stale constraints files, run scripts/compile_constraints.py: {names}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

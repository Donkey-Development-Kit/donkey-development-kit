"""Generate the framework-extra co-installability table (#769, #697).

The seven connection_kwargs()-only framework extras are kept out of `[all]`
because their current releases are not all co-installable upstream (see the
`all` extra's comment in pyproject.toml). This script records which ones are:
for every framework extra on its own, every pair, every one next to `[all]`,
and all of them together with `[all]`, it asks `uv pip compile` for a
resolution and writes the outcome to `docs/co-installability.md`.

A combination is either `yes` (it resolves) or `no` (the resolver reports
"No solution found"). Any other outcome (a network error, a timeout, a
crash) is a hard error, never a silent `no`: an incompatible combination
must fail with an explicit conflict.

Usage (from `python/`, needs `uv` on PATH and network access to the index):

    python scripts/coinstall_matrix.py           # rewrite docs/co-installability.md
    python scripts/coinstall_matrix.py --check   # exit 1 if the table changed upstream

The nightly `co-installability` job runs `--check`, so an upstream release
that makes a pair installable (or breaks one) turns the night red until the
table is regenerated. `--summary FILE` also appends the table to FILE (the
job passes `$GITHUB_STEP_SUMMARY`).
"""

from __future__ import annotations

import argparse
import itertools
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

_PYTHON_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYPROJECT = _PYTHON_ROOT / "pyproject.toml"
DEFAULT_DOC = _PYTHON_ROOT.parent / "docs" / "co-installability.md"

#: Extras that are not a framework adapter's own extra.
NON_FRAMEWORK_EXTRAS = frozenset(
    {"llm", "mcp", "a2a", "local", "otel", "cli", "test", "all", "dev"}
)
ALL = "all"
YES = "yes"
NO = "no"
_NO_SOLUTION = "No solution found"
_TIMEOUT_S = 600

#: Runs one resolve of the given extras; returns (exit code, stderr).
Resolver = Callable[[Sequence[str]], "tuple[int, str]"]


class ResolveError(RuntimeError):
    """A resolve failed for a reason other than an unsatisfiable combination."""


def framework_extras(pyproject: Mapping[str, Any]) -> list[str]:
    """Every framework adapter's extra, in pyproject order."""
    extras = pyproject["project"]["optional-dependencies"]
    return [name for name in extras if name not in NON_FRAMEWORK_EXTRAS]


def combinations(extras: Sequence[str]) -> list[tuple[str, ...]]:
    """Each extra alone, each pair, each next to ``[all]``, and all of them with ``[all]``."""
    combos: list[tuple[str, ...]] = [(e,) for e in extras]
    combos.extend(itertools.combinations(extras, 2))
    combos.extend((e, ALL) for e in extras)
    combos.append((*extras, ALL))
    return combos


def classify(returncode: int, stderr: str, combo: Sequence[str]) -> str:
    if returncode == 0:
        return YES
    if _NO_SOLUTION in stderr:
        return NO
    raise ResolveError(f"resolving {list(combo)} failed without a conflict:\n{stderr}")


def uv_resolver(python_version: str, pyproject: Path = DEFAULT_PYPROJECT) -> Resolver:
    def resolve(combo: Sequence[str]) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [
                "uv",
                "pip",
                "compile",
                str(pyproject),
                "--python-version",
                python_version,
                "--python-platform",
                "linux",
                "--no-header",
                "--no-annotate",
                "--quiet",
                "-o",
                str(Path(tmp) / "out.txt"),
            ]
            for extra in combo:
                cmd.extend(["--extra", extra])
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=_TIMEOUT_S, check=False
                )
            except subprocess.TimeoutExpired as exc:
                raise ResolveError(
                    f"resolving {list(combo)} timed out after {_TIMEOUT_S}s"
                ) from exc
            return proc.returncode, proc.stderr

    return resolve


def resolve_all(
    combos: Iterable[tuple[str, ...]], resolver: Resolver, workers: int = 8
) -> dict[tuple[str, ...], str]:
    combos = list(combos)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(resolver, combos))
    return {
        combo: classify(code, stderr, combo)
        for combo, (code, stderr) in zip(combos, outcomes, strict=True)
    }


def _cell(results: Mapping[tuple[str, ...], str], a: str, b: str) -> str:
    if a == b:
        return results[(a,)]
    return results.get((a, b)) or results[(b, a)]


def render(
    extras: Sequence[str], results: Mapping[tuple[str, ...], str], python_version: str
) -> str:
    header = ["", *[f"`{e}`" for e in extras], "`all`"]
    lines = [
        "# Framework extra co-installability",
        "",
        "<!-- GENERATED FILE: do not edit by hand. Refresh with",
        "     `python scripts/coinstall_matrix.py` from python/ (#769). -->",
        "",
        "Which framework extras resolve together. Each cell is the outcome of",
        f"`uv pip compile` for `donkey-kit[<row>,<column>]` on Python {python_version}",
        "(Linux) against the newest releases on the day the table was generated:",
        "`yes` resolves, `no` is an upstream conflict the resolver reports as",
        '"No solution found". The diagonal is the extra on its own; the last column',
        "is the extra next to `[all]`.",
        "",
        "The nightly `co-installability` job regenerates this table and fails when",
        "it changes, so a `no` here is re-checked against upstream every night.",
        "Floors only, never ceilings (ADR 0007): the SDK does not pin a framework",
        "down to make a pair resolve. Install a `no` pair in separate environments.",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for a in extras:
        row = [f"`{a}`", *[_cell(results, a, b) for b in extras], results[(a, ALL)]]
        lines.append("| " + " | ".join(row) + " |")
    together = results[tuple(extras) + (ALL,)]
    lines.extend(["", f"Every framework extra together with `[all]`: **{together}**."])
    lines.extend(
        [
            "",
            "This is uv's resolver. pip's backtracking resolver can give up",
            "(`resolution-too-deep`) on a set uv resolves; when it does, install with",
            "uv or pin the framework versions from a resolution like this one.",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the table would change")
    parser.add_argument("--pyproject", type=Path, default=DEFAULT_PYPROJECT)
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    parser.add_argument("--python-version", default="3.12")
    parser.add_argument("--summary", type=Path, help="append the table to this file")
    args = parser.parse_args(argv)

    pyproject = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))
    extras = framework_extras(pyproject)
    try:
        results = resolve_all(
            combinations(extras), uv_resolver(args.python_version, args.pyproject)
        )
    except ResolveError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    content = render(extras, results, args.python_version)
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(content)
    if args.check:
        existing = args.doc.read_text(encoding="utf-8") if args.doc.is_file() else ""
        if existing != content:
            print(content)
            print(
                f"::error::{args.doc.name} is stale: upstream releases changed which "
                "framework extras co-install. Run scripts/coinstall_matrix.py and commit."
            )
            return 1
        print(f"{args.doc.name} is current.")
        return 0
    args.doc.write_text(content, encoding="utf-8")
    print(f"wrote {args.doc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

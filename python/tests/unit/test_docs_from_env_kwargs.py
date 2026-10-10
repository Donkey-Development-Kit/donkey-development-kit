"""Docs call ``from_env()`` / ``resolve()`` only with kwargs they accept (#773).

A snippet with a misspelt field raises ``TypeError`` for whoever copies it.
``Donkey.from_env`` takes the cost-tag shorthands, ``path`` and any
``DonkeyConfig`` field (#727); ``DonkeyConfig.resolve`` takes ``path`` and any
field; ``DonkeyConfig.from_env`` takes nothing.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from donkey_kit import ConfigOverrides, Donkey, DonkeyConfig

_REPO = Path(__file__).resolve().parents[3]
_DOC_GLOBS = (
    "*.md",
    "docs/**/*.md",
    "python/*.md",
    "python/examples/**/*.md",
    "python/examples/**/*.py",
    "website/content/**/*.mdx",
)
_CALL = re.compile(r"\b(Donkey\.from_env|DonkeyConfig\.from_env|DonkeyConfig\.resolve)\(([^()]*)\)")
_KWARG = re.compile(r"\b(\w+)\s*=")


def _doc_files() -> list[Path]:
    return sorted({p for glob in _DOC_GLOBS for p in _REPO.glob(glob) if p.is_file()})


pytestmark = pytest.mark.skipif(
    not (_REPO / "website" / "content").is_dir(),
    reason="the repository docs are not included in the sdist",
)


def test_docs_from_env_calls_use_real_kwargs() -> None:
    fields = set(ConfigOverrides.__annotations__)

    def named(func: object) -> set[str]:
        params = inspect.signature(func).parameters.values()  # type: ignore[arg-type]
        return {p.name for p in params if p.kind is inspect.Parameter.KEYWORD_ONLY}

    accepted = {
        "Donkey.from_env": named(Donkey.from_env) | fields,
        "DonkeyConfig.from_env": named(DonkeyConfig.from_env),
        "DonkeyConfig.resolve": named(DonkeyConfig.resolve) | fields,
    }
    bad: list[str] = []
    for path in _doc_files():
        for match in _CALL.finditer(path.read_text(encoding="utf-8")):
            owner, args = match.groups()
            unknown = set(_KWARG.findall(args)) - accepted[owner]
            if unknown:
                bad.append(f"{path.relative_to(_REPO)}: {match.group(0)} → {sorted(unknown)}")
    assert not bad, "from_env() called with kwargs it doesn't accept:\n" + "\n".join(bad)


def test_the_guard_sees_the_docs() -> None:
    # The cost-tag snippet on the telemetry page must be found, or the glob broke.
    assert any("Donkey.from_env(team=" in p.read_text(encoding="utf-8") for p in _doc_files())

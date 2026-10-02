"""Docs call ``from_env()`` only with kwargs it accepts (#773).

A snippet like ``Donkey.from_env(telemetry_capture_content=True)`` raises
``TypeError`` for whoever copies it: ``Donkey.from_env`` takes only the cost
tags and ``on_model_substitution``, and ``DonkeyConfig.from_env`` takes nothing.
Other fields are set with ``DonkeyConfig.from_env().with_overrides(...)``.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from donkey_kit import Donkey

_REPO = Path(__file__).resolve().parents[3]
_DOC_GLOBS = (
    "*.md",
    "docs/**/*.md",
    "python/*.md",
    "python/examples/**/*.md",
    "python/examples/**/*.py",
    "website/content/**/*.mdx",
)
_CALL = re.compile(r"\b(Donkey|DonkeyConfig)\.from_env\(([^()]*)\)")
_KWARG = re.compile(r"\b(\w+)\s*=")


def _doc_files() -> list[Path]:
    return sorted({p for glob in _DOC_GLOBS for p in _REPO.glob(glob) if p.is_file()})


pytestmark = pytest.mark.skipif(
    not (_REPO / "website" / "content").is_dir(),
    reason="the repository docs are not included in the sdist",
)


def test_docs_from_env_calls_use_real_kwargs() -> None:
    accepted = {
        "Donkey": set(inspect.signature(Donkey.from_env).parameters),
        "DonkeyConfig": set(),
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
    assert any(
        "Donkey.from_env(team=" in p.read_text(encoding="utf-8") for p in _doc_files()
    )

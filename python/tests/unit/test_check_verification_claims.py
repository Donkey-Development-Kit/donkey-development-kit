"""``scripts/check_verification_claims.py``, the CI rule from #718.

Verification status lives in ``docs/verified-apis.md``; code under ``src/``
cites it and does not restate it. Only ``core/_verify.py`` may carry a status
label or a date.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_verification_claims.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ddk_check_verification_claims", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load()


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_shipped_source_has_no_claims() -> None:
    assert check.find_claims(check._DEFAULT_ROOT) == []


@pytest.mark.parametrize(
    "line",
    [
        "# Verified LIVE 2026-09-22 against ddk-injection-guard (#253).",
        '"""The LIVE-VERIFIED client_id pair."""',
        "# That route is live-verified.",
        "# The default is VERIFIED (plugin).",
        "# VERIFIED (LIVE, docs/verified-apis.md §3).",
        "# A VERIFIED-NEGATIVE result.",
        "# Agent Kill Switch (LIVE, #694).",
    ],
)
def test_claims_outside_verify_are_rejected(tmp_path: Path, line: str) -> None:
    root = _tree(tmp_path, {"core/errors.py": f"{line}\n"})
    [found] = check.find_claims(root)
    assert found.startswith("core/errors.py:1: ")
    assert found.endswith(line)
    assert check.main([str(root)]) == 1


@pytest.mark.parametrize(
    "line",
    [
        "# Header NAMES are UNVERIFIED (docs/verified-apis.md §3).",
        "# The pair is confirmed (docs/verified-apis.md §2/§3).",
        "# observed in the live-captured fixtures",
    ],
)
def test_citations_and_unverified_guards_pass(tmp_path: Path, line: str) -> None:
    root = _tree(tmp_path, {"core/errors.py": f"{line}\n"})
    assert check.find_claims(root) == []
    assert check.main([str(root)]) == 0


def test_verify_module_may_record_status(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "core/_verify.py": "# VERIFIED (LIVE 2026-09-22, #522)\n",
            "integrations/_verify.py": "# VERIFIED (LIVE 2026-09-22, #522)\n",
        },
    )
    # Only core/_verify.py is exempt, not any file with that name.
    assert check.find_claims(root) == [
        "integrations/_verify.py:1: dated claim: # VERIFIED (LIVE 2026-09-22, #522)"
    ]

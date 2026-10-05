"""The ``DONKEY_CONTRACT_EXTRA`` skip guard in ``tests/conftest.py`` (#748).

A CI job that installs a real framework sets ``DONKEY_CONTRACT_EXTRA`` to its
extra, and ``pytest_configure`` turns an ``importorskip`` of that framework into
a failure. These tests run a throwaway pytest session (``pytester``) that loads
the real hook, and import a module that does not exist under the owned
``langgraph`` package. That module is missing in every environment, so the
results do not depend on which extras are installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SUITE_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"

# Loads only the hook under test. The rest of tests/conftest.py (its autouse
# fixtures) stays out of the throwaway session.
_CONFTEST = f"""
import importlib.util

_spec = importlib.util.spec_from_file_location("_ddk_suite_conftest", {str(_SUITE_CONFTEST)!r})
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
pytest_configure = _mod.pytest_configure
"""

_OWNED_MISSING = "langgraph.ddk_748_missing"
_UNOWNED_MISSING = "crewai.ddk_748_missing"


def _module_level(modname: str) -> str:
    return f"import pytest\nmod = pytest.importorskip({modname!r})\n\ndef test_x():\n    pass\n"


def _in_test(modname: str) -> str:
    return f"import pytest\n\ndef test_x():\n    pytest.importorskip({modname!r})\n"


def _run(pytester: pytest.Pytester, source: str) -> pytest.RunResult:
    pytester.makeconftest(_CONFTEST)
    pytester.makepyfile(test_guarded=source)
    return pytester.runpytest_subprocess("-p", "no:cacheprovider")


def test_module_level_skip_of_owned_framework_fails_collection(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_CONTRACT_EXTRA", "langgraph")
    result = _run(
        pytester,
        _module_level(_OWNED_MISSING),
    )
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*The real framework install is broken*"])


def test_in_test_skip_of_owned_framework_fails(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_CONTRACT_EXTRA", "adk, langgraph")
    result = _run(
        pytester,
        _in_test(_OWNED_MISSING),
    )
    result.assert_outcomes(failed=1)


def test_skip_of_a_framework_the_job_does_not_install_still_skips(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_CONTRACT_EXTRA", "langgraph")
    result = _run(
        pytester,
        _module_level(_UNOWNED_MISSING),
    )
    result.assert_outcomes(skipped=1)


def test_without_the_variable_every_skip_stays_a_skip(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DONKEY_CONTRACT_EXTRA", raising=False)
    result = _run(
        pytester,
        _in_test(_OWNED_MISSING),
    )
    result.assert_outcomes(skipped=1)

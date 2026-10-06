"""``scripts/verify_frameworks.py``'s own verdict logic (docs/verified-apis.md §8).

The nightly matrix gates on the harness's exit code, which counts only
*installed* frameworks — so "installed" must mean the framework's distribution
is present, never "construction raised no ImportError". A renamed class raises
``ImportError: cannot import name …`` from the adapter's lazy import, and
``crewai.LLM`` re-raises any native-provider construction error as
``ImportError``; both are failures the harness exists to catch. The adapters
here are fakes, so this stays framework-free (the base-only CI job).
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import sys
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from types import ModuleType

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_frameworks.py"
# pytest's own distribution is present wherever this suite runs, so it stands in
# for an installed framework.
_INSTALLED = "pytest"
_ABSENT = "ddk-no-such-distribution"
_ADAPTER = "ddk_fake_integration"


def _load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ddk_verify_frameworks", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves the module's string annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vf = _load_harness()


def _fake_adapter(monkeypatch: pytest.MonkeyPatch, build: Callable[[str], object]) -> str:
    module = ModuleType(_ADAPTER)
    module.build = build
    monkeypatch.setitem(sys.modules, _ADAPTER, module)
    return _ADAPTER


def _raising(exc: Exception) -> Callable[[str], object]:
    def build(model: str) -> object:
        raise exc

    return build


def test_import_error_from_an_installed_framework_is_a_signature_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _fake_adapter(
        monkeypatch,
        _raising(ImportError("cannot import name 'ChatOpenAI' from 'langchain_openai'")),
    )
    res = vf.Result(framework="fake", expected_class="fake.Native")

    assert vf.check_signature(res, adapter, "build", _INSTALLED) is None
    assert res.installed is True
    assert res.verdict == "SIGNATURE FAIL"
    assert "cannot import name 'ChatOpenAI'" in res.detail


def test_absent_distribution_is_not_installed_even_when_the_adapter_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # agent_framework's adapter turns a failed lazy import into a verification
    # block; a missing package must not pass for "installed but blocked".
    adapter = _fake_adapter(
        monkeypatch, _raising(NotImplementedError("blocked on verification: fake.Native"))
    )
    res = vf.Result(framework="fake", expected_class="fake.Native")

    assert vf.check_signature(res, adapter, "build", _ABSENT) is None
    assert res.installed is False
    assert res.verdict == "NOT INSTALLED"


def test_construction_matching_the_recorded_class_is_signature_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _fake_adapter(monkeypatch, lambda model: Fraction(1))
    res = vf.Result(framework="fake", expected_class="fractions.Fraction")

    assert vf.check_signature(res, adapter, "build", _INSTALLED) == Fraction(1)
    assert res.verdict == "UNVERIFIED (signature confirmed)"


def test_exit_code_fails_on_an_import_error_from_an_installed_framework(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    adapter = _fake_adapter(
        monkeypatch, _raising(ImportError("Error importing native provider: boom"))
    )
    monkeypatch.setattr(vf, "FRAMEWORKS", [("fake", adapter, "build", "fake.Native", _INSTALLED)])
    monkeypatch.setattr(sys, "argv", ["verify_frameworks.py", "--only", "fake"])
    for var in vf.PROXY_ENV:
        monkeypatch.setenv(var, "placeholder")

    assert vf.main() == 1
    assert "SIGNATURE FAIL" in capsys.readouterr().out


def test_require_installed_passes_when_the_only_framework_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _fake_adapter(monkeypatch, lambda model: Fraction(1))
    row = ("fake", adapter, "build", "fractions.Fraction", _INSTALLED)
    monkeypatch.setattr(vf, "FRAMEWORKS", [row])
    monkeypatch.setattr(
        sys, "argv", ["verify_frameworks.py", "--only", "fake", "--require-installed"]
    )
    for var in vf.PROXY_ENV:
        monkeypatch.setenv(var, "placeholder")

    assert vf.main() == 0


def test_require_installed_fails_when_the_only_framework_is_not_installed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # #748: a CI leg that installs one framework and calls --only <fw> must not
    # exit 0 just because the install silently failed (wrong extra name, a
    # resolver skip, …) — that reads as NOT INSTALLED without this flag.
    adapter = _fake_adapter(monkeypatch, lambda model: Fraction(1))
    row = ("fake", adapter, "build", "fractions.Fraction", _ABSENT)
    monkeypatch.setattr(vf, "FRAMEWORKS", [row])
    monkeypatch.setattr(
        sys, "argv", ["verify_frameworks.py", "--only", "fake", "--require-installed"]
    )
    for var in vf.PROXY_ENV:
        monkeypatch.setenv(var, "placeholder")

    assert vf.main() == 1
    out = capsys.readouterr()
    assert "NOT INSTALLED" in out.err
    assert "fake" in out.err


def test_require_installed_is_a_no_op_without_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without --only, "not installed" is the expected, ad hoc "whatever I have"
    # outcome, not a broken-install signal, so --require-installed alone (no
    # --only) must not turn a merely-absent framework into a failure.
    adapter = _fake_adapter(monkeypatch, lambda model: Fraction(1))
    row = ("fake", adapter, "build", "fractions.Fraction", _ABSENT)
    monkeypatch.setattr(vf, "FRAMEWORKS", [row])
    monkeypatch.setattr(sys, "argv", ["verify_frameworks.py", "--require-installed"])
    for var in vf.PROXY_ENV:
        monkeypatch.setenv(var, "placeholder")

    assert vf.main() == 0


def test_each_framework_checks_a_distribution_its_extra_installs() -> None:
    # The nightly matrix installs the framework's extra and then runs the harness;
    # a distribution the extra doesn't ship would read as NOT INSTALLED and exit 0.
    requirements = [Requirement(r) for r in importlib.metadata.requires("donkey-kit") or []]
    for key, _, _, _, distribution in vf.FRAMEWORKS:
        # A dotted key is a second factory of the same framework (adk.gemini).
        framework = key.partition(".")[0]
        extra = {
            canonicalize_name(r.name)
            for r in requirements
            if r.marker and r.marker.evaluate({"extra": canonicalize_name(framework)})
        }
        assert canonicalize_name(distribution) in extra, (key, distribution, sorted(extra))

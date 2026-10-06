"""Strict example smoke (#748): ``DONKEY_STRICT_EXAMPLE=1`` makes an example fail loudly.

By default an example prints install guidance and exits 0 when its framework is
missing, which is right for a user and wrong for CI: a broken install would
read as a pass. In strict mode the ``ImportError`` must propagate out of
``main()`` so the process exits non-zero.

The framework is made "missing" by pointing its roster probe at a module that
does not exist, so the result does not depend on which extras are installed.
Strict mode drives ``build`` through the public conformance kit (in-process,
no network), so this needs no extra beyond the base install.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest

from donkey_kit.integrations import ADAPTERS

_EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
# Every example with a strict branch; quickstart and langgraph own their smoke.
_SHALLOW = sorted(attr for attr in ADAPTERS if attr != "langgraph")


def _load(attr: str) -> object:
    name = f"ddk_strict_example_{attr}"
    spec = importlib.util.spec_from_file_location(name, _EXAMPLES / attr / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("attr", _SHALLOW)
def test_strict_example_with_its_framework_missing_raises(
    attr: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_STRICT_EXAMPLE", "1")
    monkeypatch.setitem(
        ADAPTERS, attr, dataclasses.replace(ADAPTERS[attr], probe=("ddk_no_such_module_748",))
    )
    module = _load(attr)
    with pytest.raises(ImportError):
        module.main()  # type: ignore[attr-defined]


@pytest.mark.parametrize("attr", _SHALLOW)
def test_default_example_with_its_framework_missing_still_exits_zero(
    attr: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DONKEY_STRICT_EXAMPLE", raising=False)
    monkeypatch.setitem(
        ADAPTERS, attr, dataclasses.replace(ADAPTERS[attr], probe=("ddk_no_such_module_748",))
    )
    module = _load(attr)
    module.main()  # type: ignore[attr-defined]  # prints guidance, returns
    assert capsys.readouterr().out

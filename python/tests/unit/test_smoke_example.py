"""``scripts/smoke_example.py`` fails loudly when an example's framework is missing (#748).

Run by hand, an example prints install guidance and exits 0 when its framework
is missing. The CI smoke must instead raise, so a broken install never reads as
a pass. The framework is made "missing" by pointing its roster probe at a
module that does not exist, so the result does not depend on which extras are
installed. Both paths are checked: the script raises, the example still exits 0.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from donkey_kit.integrations import ADAPTERS

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_example.py"
_EXAMPLES = sorted(ADAPTERS)


def _load_script() -> ModuleType:
    name = "ddk_smoke_example_script"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _hide_framework(attr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        ADAPTERS, attr, dataclasses.replace(ADAPTERS[attr], probe=("ddk_no_such_module_748",))
    )


@pytest.mark.parametrize("attr", _EXAMPLES)
def test_smoke_with_the_framework_missing_raises(
    attr: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hide_framework(attr, monkeypatch)
    script = _load_script()
    with pytest.raises(ImportError):
        asyncio.run(script.smoke(attr))


@pytest.mark.parametrize("attr", sorted(set(_EXAMPLES) - {"langgraph"}))
def test_example_with_the_framework_missing_still_exits_zero(
    attr: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # LangGraph's example owns a different missing-framework path (its own smoke).
    _hide_framework(attr, monkeypatch)
    example = _load_script().load_example(attr)
    example.main()  # prints guidance, returns
    assert capsys.readouterr().out

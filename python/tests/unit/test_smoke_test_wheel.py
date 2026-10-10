"""``scripts/smoke_test_wheel.py`` installs a wheel cleanly and imports it (#767).

Both publish workflows run it on the exact wheel they are about to upload. The
real wheel needs httpx from an index, so these tests build a stand-in wheel
with no dependencies and install it with ``--no-index``: offline, and still a
real virtualenv, a real ``pip install`` and the real import probe.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_test_wheel.py"


def _load() -> ModuleType:
    name = "ddk_smoke_test_wheel"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load()


def _record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(data)}"


def _wheel(tmp_path: Path, *, version: str, declared: str) -> Path:
    """A minimal, dependency-free ``donkey-kit`` wheel whose ``__version__`` is ``declared``."""
    dist_info = f"donkey_kit-{version}.dist-info"
    files = {
        "donkey_kit/__init__.py": (
            f'__version__ = "{declared}"\n\n\nclass Donkey:\n    """Stand-in."""\n'
        ).encode(),
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.4\nName: donkey-kit\nVersion: {version}\n\n"
        ).encode(),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: ddk-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = [_record_line(name, data) for name, data in files.items()]
    record.append(f"{dist_info}/RECORD,,")
    path = tmp_path / f"donkey_kit-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as whl:
        for name, data in files.items():
            whl.writestr(name, data)
        whl.writestr(f"{dist_info}/RECORD", "\n".join(record) + "\n")
    return path


def test_wheel_version_reads_the_filename() -> None:
    assert smoke.wheel_version(Path("donkey_kit-0.1.3.dev0-py3-none-any.whl")) == "0.1.3.dev0"
    with pytest.raises(ValueError, match="not a wheel"):
        smoke.wheel_version(Path("donkey_kit-0.1.3.tar.gz"))


@pytest.mark.timeout(180)
def test_a_good_wheel_installs_and_imports(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    wheel = _wheel(tmp_path, version="9.8.7", declared="9.8.7")
    assert smoke.main([str(wheel), "--", "--no-index"]) == 0
    assert "donkey-kit 9.8.7 installs and imports from" in capfd.readouterr().out


@pytest.mark.timeout(180)
def test_a_wheel_whose_version_disagrees_fails(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    # The wheel metadata says 9.8.7, but the code inside reports 9.8.6: the
    # exact drift a two-file version allowed.
    wheel = _wheel(tmp_path, version="9.8.7", declared="9.8.6")
    assert smoke.main([str(wheel), "--", "--no-index"]) == 1
    assert "__version__ '9.8.6', installed '9.8.7'" in capfd.readouterr().err


@pytest.mark.timeout(180)
def test_an_unexpected_version_fails(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    wheel = _wheel(tmp_path, version="9.8.7", declared="9.8.7")
    assert smoke.main([str(wheel), "--expect-version", "9.8.8", "--", "--no-index"]) == 1
    assert "expected '9.8.8'" in capfd.readouterr().err

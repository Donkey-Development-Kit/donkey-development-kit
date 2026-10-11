"""One version source, and a publish that fails when the tag disagrees (#767).

``pyproject.toml`` declares the version dynamic and hatch reads it from
``__version__``, so the two can no longer drift apart (an editable install was
seen reporting 0.1.1.dev0 while ``__version__`` said 0.1.1.dev2).
``scripts/check_release_version.py`` is the gate both publish workflows run
before any upload: tag equals ``v<version>``, prod takes only a final version,
and every built dist carries the declared version in its own metadata.

Framework-free: it reads files and builds tiny dists in ``tmp_path``.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

_PYTHON_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _PYTHON_ROOT / "scripts" / "check_release_version.py"


def _load() -> ModuleType:
    name = "ddk_check_release_version"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load()


def _pyproject() -> dict[str, object]:
    data = tomllib.loads((_PYTHON_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _metadata(version: str) -> bytes:
    return f"Metadata-Version: 2.4\nName: donkey-kit\nVersion: {version}\n\n".encode()


def _wheel(tmp_path: Path, version: str, *, filename_version: str | None = None) -> Path:
    path = tmp_path / f"donkey_kit-{filename_version or version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as whl:
        whl.writestr(f"donkey_kit-{version}.dist-info/METADATA", _metadata(version))
    return path


def _sdist(tmp_path: Path, version: str) -> Path:
    path = tmp_path / f"donkey_kit-{version}.tar.gz"
    raw = _metadata(version)
    with tarfile.open(path, "w:gz") as sdist:
        info = tarfile.TarInfo(f"donkey_kit-{version}/PKG-INFO")
        info.size = len(raw)
        sdist.addfile(info, io.BytesIO(raw))
    return path


# --- one version source -----------------------------------------------------


def test_pyproject_reads_the_version_from_init_py() -> None:
    data = _pyproject()
    project = data["project"]
    assert isinstance(project, dict)
    assert "version" not in project, "the version is declared in __init__.py only"
    assert "version" in project["dynamic"]
    tool = data["tool"]
    assert isinstance(tool, dict)
    assert tool["hatch"]["version"] == {"path": "src/donkey_kit/__init__.py"}


def test_init_py_declares_one_ladder_version() -> None:
    version = check.declared_version()
    assert check.problems(version) == []


def test_declared_version_is_the_runtime_version() -> None:
    import donkey_kit

    assert check.declared_version() == donkey_kit.__version__


def test_bump_script_edits_only_the_one_version_source() -> None:
    script = _PYTHON_ROOT.parent / "scripts" / "bump-version.sh"
    if not script.is_file():
        pytest.skip("not a repo checkout (no scripts/bump-version.sh)")
    text = script.read_text(encoding="utf-8")
    assert 'INIT_PY="python/src/donkey_kit/__init__.py"' in text
    assert "PYPROJECT" not in text, "the bump touches __init__.py only"
    # The <type>/<issue#>-<slug> branch convention, not release/bump-<v>.
    assert 'BRANCH="chore/${ISSUE}-bump-version-${NEW//./-}"' in text
    assert "release/bump" not in text


def test_declared_version_rejects_zero_or_two_lines(tmp_path: Path) -> None:
    init = tmp_path / "__init__.py"
    init.write_text('"""no version here."""\n', encoding="utf-8")
    with pytest.raises(ValueError, match="found 0"):
        check.declared_version(init)
    init.write_text('__version__ = "1.0.0"\n__version__ = "1.0.1"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="found 2"):
        check.declared_version(init)
    init.write_text('__version__ = "1.2.3.dev4"\n', encoding="utf-8")
    assert check.declared_version(init) == "1.2.3.dev4"


# --- the version / tag / dist gate -----------------------------------------


@pytest.mark.parametrize("version", ["0.1.2", "0.1.3.dev0", "1.0.0a1", "1.0.0b2", "1.0.0rc10"])
def test_ladder_versions_pass(version: str) -> None:
    assert check.problems(version, tag=f"v{version}") == []


@pytest.mark.parametrize("version", ["0.1.0-alpha.1", "0.1", "0.1.0.dev", "v0.1.0", "01.0.0"])
def test_unnormalised_versions_fail(version: str) -> None:
    assert check.problems(version) != []


@pytest.mark.parametrize("tag", ["0.1.2", "v0.1.3", "v0.1.2rc1", "release-0.1.2", "V0.1.2"])
def test_a_tag_that_is_not_v_version_fails(tag: str) -> None:
    (problem,) = check.problems("0.1.2", tag=tag)
    assert "expected 'v0.1.2'" in problem


@pytest.mark.parametrize("version", ["0.1.3.dev0", "0.1.3a1", "0.1.3rc1"])
def test_final_rejects_a_pre_release(version: str) -> None:
    (problem,) = check.problems(version, tag=f"v{version}", final=True)
    assert "pre-release" in problem


def test_final_accepts_a_final_release() -> None:
    assert check.problems("0.1.3", tag="v0.1.3", final=True) == []


def test_dists_that_carry_the_version_pass(tmp_path: Path) -> None:
    dists = [_wheel(tmp_path, "0.1.3"), _sdist(tmp_path, "0.1.3")]
    assert check.problems("0.1.3", tag="v0.1.3", final=True, dists=dists) == []


def test_a_dist_built_at_another_version_fails(tmp_path: Path) -> None:
    stale = _wheel(tmp_path, "0.1.2")
    (problem,) = check.problems("0.1.3", dists=[stale, _sdist(tmp_path, "0.1.3")])
    assert stale.name in problem and "'0.1.2'" in problem


def test_the_dist_metadata_wins_over_its_filename(tmp_path: Path) -> None:
    renamed = _wheel(tmp_path, "0.1.2", filename_version="0.1.3")
    assert check.problems("0.1.3", dists=[renamed]) != []


def test_an_unreadable_dist_fails(tmp_path: Path) -> None:
    junk = tmp_path / "donkey_kit-0.1.3-py3-none-any.whl"
    junk.write_bytes(b"not a zip")
    other = tmp_path / "donkey_kit-0.1.3.zip"
    other.write_bytes(b"")
    found = check.problems("0.1.3", dists=[junk, other])
    assert len(found) == 2
    assert all("cannot read its version" in p for p in found)


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    version = check.declared_version()
    wheel = _wheel(tmp_path, version)
    assert check.main(["--tag", f"v{version}", str(wheel)]) == 0
    assert version in capsys.readouterr().out
    assert check.main(["--tag", "v0.0.0", str(wheel)]) == 1
    assert "does not match" in capsys.readouterr().err

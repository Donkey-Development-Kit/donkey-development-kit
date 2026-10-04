"""``scripts/scrub_fixtures.py``: the fixture scrub and the CI denylist (#821).

Captures carry the capturing tenant's identifiers; some fixtures ship in the
immutable wheel. The scrub replaces them with deterministic placeholders, and
``--check`` fails CI on any that remain, in the tree or in a built dist.

Planted values are assembled at runtime so this file passes the check itself.
"""

from __future__ import annotations

import importlib.util
import io
import tarfile
import uuid
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scrub_fixtures.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ddk_scrub_fixtures", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scrub = _load()

_IN_CHECKOUT = (scrub._DEFAULT_ROOT / ".git").exists()

_PLATFORM = "cloud" + "hub.io"
_TENANT_HOST = f"shared-omni-gateway-q1w2e3.r4t5y6-1.usa-e2.{_PLATFORM}"
_PROVIDER = "openai-project: proj_" + "Ab3dE5gH7jK9mN1pQ3sT5vX7"


def _real_uuid() -> str:
    return str(uuid.uuid4())


def _kinds(text: str) -> list[str]:
    return [kind for _, kind, _ in scrub.find_in_text(text)]


def test_planted_uuid_fails_the_check(tmp_path: Path) -> None:
    (tmp_path / "f.json").write_text(f'{{"organizationId": "{_real_uuid()}"}}\n')
    assert scrub.main(["--check", str(tmp_path)]) == 1


def test_planted_platform_host_fails_the_check(tmp_path: Path) -> None:
    (tmp_path / "request.http").write_text(f"Host: {_TENANT_HOST}\n")
    assert scrub.main(["--check", str(tmp_path)]) == 1
    # Any host on the platform is rejected, not only the <name>-<suffix> shape.
    assert _kinds(f"https://myapp.{_PLATFORM}/") == ["platform host"]


def test_planted_provider_account_header_fails_the_check() -> None:
    assert _kinds(_PROVIDER) == ["openai-project value"]


def test_finding_shows_only_a_value_prefix(tmp_path: Path) -> None:
    value = _real_uuid()
    (tmp_path / "f.txt").write_text(value)
    (finding,) = scrub.check([tmp_path])
    assert value[:8] in finding
    assert value not in finding


def test_placeholders_and_allowlisted_values_pass() -> None:
    text = "\n".join(
        [
            "org 00000000-0000-4000-8000-0123456789ab",
            next(iter(scrub.ALLOWED_UUIDS)),
            "Host: shared-omni-gateway.example.invalid",
            "openai-project: proj_example0123456789abcd",
        ]
    )
    assert _kinds(text) == []


def test_scrub_is_deterministic_idempotent_and_clears_every_finding() -> None:
    real = _real_uuid()
    text = f"x-correlation-id: {real}\nenv {real}\nHost: {_TENANT_HOST}\n{_PROVIDER}\n"
    once = scrub.scrub_text(text)
    assert _kinds(once) == []
    assert scrub.scrub_text(once) == once
    # The same real value always maps to the same placeholder.
    assert once.count(scrub.scrub_text(real)) == 2
    assert "shared-omni-gateway.example.invalid" in once
    # Provider ids keep their prefix and length.
    (line,) = [ln for ln in once.splitlines() if ln.startswith("openai-project")]
    assert len(line) == len(_PROVIDER)
    assert line.startswith("openai-project: proj_example")


def test_scrub_preserves_bytes_around_replacements(tmp_path: Path) -> None:
    path = tmp_path / "reject.headers.txt"
    path.write_bytes(f"HTTP/1.1 403 Forbidden\r\nx-correlation-id: {_real_uuid()}\r\n".encode())
    assert scrub.scrub([path]) == [path]
    data = path.read_bytes()
    assert data.count(b"\r\n") == 2
    assert not data.endswith(b"\r\n\n")
    assert scrub.check([path]) == []


def test_built_wheel_and_sdist_are_checked(tmp_path: Path) -> None:
    planted = f"Host: {_TENANT_HOST}\n".encode()
    wheel = tmp_path / "donkey_kit-0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("donkey_kit/simulator/_fixtures/x.headers.txt", planted)
    sdist = tmp_path / "donkey_kit-0.tar.gz"
    with tarfile.open(sdist, "w:gz") as tf:
        info = tarfile.TarInfo("donkey_kit-0/tests/fixtures/x.http")
        info.size = len(planted)
        tf.addfile(info, io.BytesIO(planted))
    found = scrub.check([wheel, sdist])
    assert len(found) == 2
    assert any("!donkey_kit/simulator/_fixtures/x.headers.txt" in f for f in found)
    with pytest.raises(SystemExit):
        scrub.scrub([wheel])


@pytest.mark.skipif(not _IN_CHECKOUT, reason="needs a git checkout")
def test_tracked_tree_carries_no_tenant_identifier() -> None:
    assert scrub.check(scrub.tracked_files(scrub._DEFAULT_ROOT)) == []

"""``[targets]`` profile discovery for ``GatewayTarget.from_env`` (#815).

``[targets]`` must be read from the same files, in the same order, as
``[donkey]`` — including ``$XDG_CONFIG_HOME/.donkey-kit.toml`` when the working
directory has no config of its own."""

from __future__ import annotations

from pathlib import Path

import pytest

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.governance import GatewayTarget

_SANDBOX = """
[donkey]
llm_proxy_url = "https://xdg.example/proxy/"

[targets.sandbox]
mode = "managed"
base_url = "https://sandbox.example"
environment = "Sandbox"
"""


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    project, xdg = tmp_path / "project", tmp_path / "xdg"
    project.mkdir()
    xdg.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("DONKEY_LLM_PROXY_URL", raising=False)
    monkeypatch.setenv("DONKEY_TARGET", "sandbox")
    return project, xdg


def test_targets_are_read_from_the_xdg_config(dirs: tuple[Path, Path]) -> None:
    _, xdg = dirs
    (xdg / ".donkey-kit.toml").write_text(_SANDBOX)

    target = GatewayTarget.from_env()

    assert target.mode == "managed"
    assert target.base_url == "https://sandbox.example"
    assert target.environment == "Sandbox"
    assert target.connected is True
    # ...the same file [donkey] is read from.
    assert DonkeyConfig.from_env().llm_proxy_url == "https://xdg.example/proxy/"


def test_project_config_shadows_the_xdg_config(dirs: tuple[Path, Path]) -> None:
    project, xdg = dirs
    (xdg / ".donkey-kit.toml").write_text(_SANDBOX)
    (project / ".donkey-kit.toml").write_text(
        '[targets.sandbox]\nmode = "managed"\nbase_url = "https://project.example"\n'
    )

    assert GatewayTarget.from_env().base_url == "https://project.example"


def test_local_overlay_merges_over_project_targets(dirs: tuple[Path, Path]) -> None:
    project, _ = dirs
    (project / ".donkey-kit.toml").write_text(
        '[targets.sandbox]\nmode = "managed"\nbase_url = "https://project.example"\n'
    )
    (project / ".donkey-kit.local.toml").write_text(
        '[targets.sandbox]\nenvironment = "Mine"\n'
    )

    target = GatewayTarget.from_env()

    assert target.base_url == "https://project.example"
    assert target.environment == "Mine"


def test_missing_profile_is_a_config_error(dirs: tuple[Path, Path]) -> None:
    with pytest.raises(ConfigError, match=r"\[targets\.sandbox\]"):
        GatewayTarget.from_env()

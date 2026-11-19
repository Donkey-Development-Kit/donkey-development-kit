"""``donkey init``'s output and destination contract (#1746).

What the generated file holds (every supported connection mode, escaped,
secret-free, URLs included) and where it may be written (no symlinks, no
directories, no secret holders, no clobbering a file that appears mid-write,
``--force`` as a documented full regeneration). Each refusal asserts the exit
status, both streams and the destination's bytes separately.

Framework-free: needs only the ``dev`` group (typer). Runs in the base-only CI job.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from donkey_kit.cli import _files, app

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

runner = CliRunner()

_SENTINEL = "s3ntinel-v4lue"

_ENV = (
    "ANYPOINT_CLIENT_ID",
    "ANYPOINT_CLIENT_SECRET",
    "ANYPOINT_ORG_ID",
    "ANYPOINT_ENV",
    "ANYPOINT_REGION",
    "ANYPOINT_BASE_URL",
    "DONKEY_LLM_PROXY_URL",
    "DONKEY_LLM_PROXY_CLIENT_ID",
    "DONKEY_LLM_PROXY_CLIENT_SECRET",
    "DONKEY_LLM_PROXY_KEY",
    "DONKEY_LLM_PROXY_KEY_HEADER",
    "DONKEY_LLM_PROXY_AUTH",
    "DONKEY_LLM_PROXY_WALLET_CLIENT_ID",
    "DONKEY_APP_NAME",
    "DONKEY_BUSINESS_GROUP",
)


@pytest.fixture
def cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A pristine working directory and user-config home, no donkey env vars."""
    work = tmp_path / "work"
    work.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)
    return work


def _streams(result: Result) -> str:
    return result.stdout + result.stderr


def _table(path: Path) -> dict[str, object]:
    table = tomllib.loads(path.read_text(encoding="utf-8"))["donkey"]
    assert isinstance(table, dict)
    return table


def _no_staged_files(directory: Path) -> bool:
    return not [p for p in directory.iterdir() if p.name.endswith(".tmp")]


# --- connection modes ---------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (
            {"DONKEY_LLM_PROXY_AUTH": "client-id", "DONKEY_LLM_PROXY_CLIENT_ID": "cid"},
            {"llm_proxy_auth": "client-id", "llm_proxy_client_id": "cid"},
        ),
        (
            {"DONKEY_LLM_PROXY_AUTH": "jwt", "DONKEY_LLM_PROXY_WALLET_CLIENT_ID": "wallet-1"},
            {"llm_proxy_auth": "jwt", "llm_proxy_wallet_client_id": "wallet-1"},
        ),
        (
            {"DONKEY_LLM_PROXY_AUTH": "bearer"},
            {"llm_proxy_auth": "bearer"},
        ),
        (
            {
                "DONKEY_LLM_PROXY_CLIENT_ID": "cid",
                "DONKEY_LLM_PROXY_KEY_HEADER": "x-provider-key",
            },
            {"llm_proxy_client_id": "cid", "llm_proxy_key_header": "x-provider-key"},
        ),
    ],
    ids=["client-id", "jwt", "bearer", "dynamic-key-header"],
)
def test_every_connection_mode_round_trips_without_secrets(
    monkeypatch: pytest.MonkeyPatch, cwd: Path, env: dict[str, str], expected: dict[str, str]
) -> None:
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://gw.example.test/proxy/")
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    for var in ("ANYPOINT_CLIENT_SECRET", "DONKEY_LLM_PROXY_CLIENT_SECRET", "DONKEY_LLM_PROXY_KEY"):
        monkeypatch.setenv(var, f"{var}-{_SENTINEL}")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, _streams(result)
    written = cwd / ".donkey-kit.toml"
    table = _table(written)
    for key, value in expected.items():
        assert table[key] == value
    assert _SENTINEL not in written.read_text(encoding="utf-8")
    assert _SENTINEL not in _streams(result)
    for secret in ("client_secret", "llm_proxy_client_secret", "llm_proxy_key"):
        assert secret not in table


@pytest.mark.parametrize("mode", ["jwt", "bearer"])
def test_token_modes_say_the_token_is_not_config(
    monkeypatch: pytest.MonkeyPatch, cwd: Path, mode: str
) -> None:
    monkeypatch.setenv("DONKEY_LLM_PROXY_AUTH", mode)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, _streams(result)
    text = (cwd / ".donkey-kit.toml").read_text(encoding="utf-8")
    assert "Donkey(llm_auth=...)" in text
    assert "CLI has none" in text


def test_jwt_mode_reports_the_wallet_selector_as_missing(
    monkeypatch: pytest.MonkeyPatch, cwd: Path
) -> None:
    monkeypatch.setenv("DONKEY_LLM_PROXY_AUTH", "jwt")
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://gw.example.test/")

    result = runner.invoke(app, ["--json", "init"])

    assert result.exit_code == 0, _streams(result)
    missing = json.loads(result.stdout)["missing"]
    assert any(item.startswith("llm_proxy_wallet_client_id") for item in missing)
    assert not any(item.startswith("llm_proxy_client_secret") for item in missing)


def test_default_output_has_no_auth_mode_key(monkeypatch: pytest.MonkeyPatch, cwd: Path) -> None:
    """Backward compatibility: an unset mode writes what init always wrote."""
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, _streams(result)
    table = _table(cwd / ".donkey-kit.toml")
    assert "llm_proxy_auth" not in table
    assert set(table) == {"environment", "region", "llm_proxy_client_id"}


def test_mode_from_the_local_overlay_is_carried(cwd: Path) -> None:
    (cwd / ".donkey-kit.local.toml").write_text(
        f'[donkey]\nllm_proxy_auth = "jwt"\nllm_proxy_key = "{_SENTINEL}"\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, _streams(result)
    written = cwd / ".donkey-kit.toml"
    assert _table(written)["llm_proxy_auth"] == "jwt"
    assert _SENTINEL not in written.read_text(encoding="utf-8")
    assert _SENTINEL not in _streams(result)


# --- escaping -----------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        'quote " inside',
        "back\\slash",
        "new\nline",
        "tab\tstop",
        "carriage\rreturn",
        "bell\x07 and \x01 and \x1f",
        "delete\x7f",
        "form\ffeed\bback",
        "unicode — ✓ 日本",
        "\\u0041 stays literal",
    ],
)
def test_strings_round_trip_every_escape(
    monkeypatch: pytest.MonkeyPatch, cwd: Path, value: str
) -> None:
    monkeypatch.setenv("DONKEY_APP_NAME", value)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, _streams(result)
    assert _table(cwd / ".donkey-kit.toml")["application_name"] == value


def test_value_from_the_project_file_round_trips_on_force(cwd: Path) -> None:
    project = cwd / ".donkey-kit.toml"
    project.write_text('[donkey]\napplication_name = "line\\nbreak \\"q\\""\n', encoding="utf-8")

    result = runner.invoke(app, ["init", "--force"])

    assert result.exit_code == 0, _streams(result)
    assert _table(project)["application_name"] == 'line\nbreak "q"'


def test_value_that_is_not_unicode_text_is_refused(
    monkeypatch: pytest.MonkeyPatch, cwd: Path
) -> None:
    # An environment variable holding invalid UTF-8 bytes decodes to a lone surrogate.
    monkeypatch.setenv("DONKEY_APP_NAME", "bad\udcffvalue")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "not valid Unicode text" in result.stderr
    assert "bad" not in result.stderr
    assert not (cwd / ".donkey-kit.toml").exists()
    assert _no_staged_files(cwd)


# --- URLs carrying credentials ------------------------------------------------

_CREDENTIAL_URLS = [
    f"https://user:{_SENTINEL}@gw.example.test/",
    f"https://gw.example.test/?api_key={_SENTINEL}",
    f"https://gw.example.test/#access_token={_SENTINEL}",
    f"https://user:{_SENTINEL}@gw.example.test:notaport/",
]


@pytest.mark.parametrize("url", _CREDENTIAL_URLS)
@pytest.mark.parametrize("field", ["llm_proxy_url", "base_url"])
def test_credential_url_from_env_is_refused(
    monkeypatch: pytest.MonkeyPatch, cwd: Path, url: str, field: str
) -> None:
    var = "DONKEY_LLM_PROXY_URL" if field == "llm_proxy_url" else "ANYPOINT_BASE_URL"
    monkeypatch.setenv(var, url)

    for args in (["init"], ["--json", "init"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 1, args
        assert result.stdout == ""
        assert f"{field} (from env)" in result.stderr
        assert _SENTINEL not in result.stderr
    assert not (cwd / ".donkey-kit.toml").exists()
    assert _no_staged_files(cwd)


@pytest.mark.parametrize("url", _CREDENTIAL_URLS)
@pytest.mark.parametrize("source", ["project", "local", "user"])
def test_credential_url_from_any_file_is_refused(
    cwd: Path, tmp_path: Path, url: str, source: str
) -> None:
    body = f"[donkey]\nllm_proxy_url = {json.dumps(url)}\n"
    path = {
        "project": cwd / ".donkey-kit.toml",
        "local": cwd / ".donkey-kit.local.toml",
        "user": tmp_path / "home" / ".donkey-kit.toml",
    }[source]
    path.write_text(body, encoding="utf-8")
    project = cwd / ".donkey-kit.toml"
    before = project.read_bytes() if project.exists() else None

    result = runner.invoke(app, ["init", "--force"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "llm_proxy_url" in result.stderr
    assert _SENTINEL not in result.stderr
    assert (project.read_bytes() if project.exists() else None) == before
    assert _no_staged_files(cwd)


# --- destinations -------------------------------------------------------------


@pytest.mark.parametrize("force", [False, True])
def test_dangling_symlink_is_never_followed(cwd: Path, tmp_path: Path, force: bool) -> None:
    pointee = tmp_path / "elsewhere.toml"
    link = cwd / ".donkey-kit.toml"
    link.symlink_to(pointee)

    result = runner.invoke(app, ["init", *(["--force"] if force else [])])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "symlink" in result.stderr
    assert link.is_symlink()
    assert not pointee.exists()


def test_symlink_to_a_file_is_not_replaced_even_with_force(cwd: Path, tmp_path: Path) -> None:
    pointee = tmp_path / "shared.toml"
    pointee.write_text('[donkey]\nclient_id = "kept"\n', encoding="utf-8")
    (cwd / ".donkey-kit.toml").symlink_to(pointee)

    result = runner.invoke(app, ["init", "--force"])

    assert result.exit_code == 1
    assert "symlink" in result.stderr
    assert pointee.read_text(encoding="utf-8") == '[donkey]\nclient_id = "kept"\n'


@pytest.mark.parametrize("force", [False, True])
def test_directory_destination_is_refused(cwd: Path, force: bool) -> None:
    (cwd / ".donkey-kit.toml").mkdir()

    result = runner.invoke(app, ["init", *(["--force"] if force else [])])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "is a directory" in result.stderr


@pytest.mark.parametrize(
    "name", [".donkey-kit.local.toml", ".env", ".env.production", ".envrc", "donkey.lock.local"]
)
def test_secret_holder_is_never_a_destination(cwd: Path, name: str) -> None:
    result = runner.invoke(app, ["--config", str(cwd / name), "init", "--force"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "where secrets are kept" in result.stderr
    assert not (cwd / name).exists()


def test_custom_path_with_spaces_and_new_parents(cwd: Path) -> None:
    target = cwd / "my configs" / "deep dir" / "donkey config.toml"

    result = runner.invoke(app, ["--json", "--config", str(target), "init"])

    assert result.exit_code == 0, _streams(result)
    payload = json.loads(result.stdout)
    assert (payload["path"], payload["written"]) == (str(target), True)
    assert _table(target)["environment"] == "Sandbox"
    assert not (cwd / ".donkey-kit.toml").exists()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX non-root")
def test_permission_failure_is_reported_without_traceback(cwd: Path) -> None:
    locked = cwd / "locked"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = runner.invoke(app, ["--config", str(locked / "cfg.toml"), "init"])
    finally:
        locked.chmod(stat.S_IRWXU)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Could not write" in result.stderr
    assert "Traceback" not in _streams(result)
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert list(locked.iterdir()) == []


def test_file_created_mid_write_is_not_clobbered(
    monkeypatch: pytest.MonkeyPatch, cwd: Path
) -> None:
    target = cwd / ".donkey-kit.toml"
    stage = _files._stage

    def racing(path: Path, content: str) -> Path:
        staged = stage(path, content)
        target.write_text("# theirs\n", encoding="utf-8")  # a competing creator wins
        return staged

    monkeypatch.setattr(_files, "_stage", racing)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "created by something else" in result.stderr
    assert target.read_text(encoding="utf-8") == "# theirs\n"
    assert _no_staged_files(cwd)


def test_failed_replace_leaves_the_original_and_no_staged_file(
    monkeypatch: pytest.MonkeyPatch, cwd: Path
) -> None:
    target = cwd / ".donkey-kit.toml"
    target.write_text('[donkey]\nclient_id = "original"\n', encoding="utf-8")

    def broken(src: object, dst: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(_files.os, "replace", broken)

    result = runner.invoke(app, ["init", "--force"])

    assert result.exit_code == 1
    assert "No space left on device" in result.stderr
    assert target.read_text(encoding="utf-8") == '[donkey]\nclient_id = "original"\n'
    assert _no_staged_files(cwd)


# --- --force is full regeneration, the bare re-run a no-op -------------------

_DECLARED = (
    '[donkey]\nclient_id = "hand"\n\n'
    '[donkey.agents.triage]\nprotocol = "a2a_v1"\n\n'
    '[donkey.publication]\nversion = "1.0.0"\n\n'
    "[tool.other]\nx = 1\n"
)


def test_bare_rerun_keeps_a_file_with_declarations_byte_for_byte(cwd: Path) -> None:
    target = cwd / ".donkey-kit.toml"
    target.write_text(_DECLARED, encoding="utf-8")

    result = runner.invoke(app, ["--json", "init"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"path": str(target), "written": False, "missing": []}
    assert target.read_text(encoding="utf-8") == _DECLARED


def test_force_regenerates_in_full_and_names_what_it_dropped(
    monkeypatch: pytest.MonkeyPatch, cwd: Path
) -> None:
    target = cwd / ".donkey-kit.toml"
    target.write_text(_DECLARED, encoding="utf-8")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "from-env")

    result = runner.invoke(app, ["--json", "init", "--force"])

    assert result.exit_code == 0, _streams(result)
    assert set(json.loads(result.stdout)) == {"path", "written", "missing"}
    assert "[donkey.agents], [donkey.publication], [tool]" in result.stderr
    data = tomllib.loads(target.read_text(encoding="utf-8"))
    assert set(data) == {"donkey"}
    assert data["donkey"]["client_id"] == "from-env"
    assert "agents" not in data["donkey"]


def test_force_replaces_an_unparseable_file_and_says_so(cwd: Path) -> None:
    # A --config destination is not read as input, so only the dropped-table
    # report sees it. (Invalid UTF-8 in the working directory's own project file
    # is a config-read failure, normalized by #1750.)
    target = cwd / "generated.toml"
    target.write_bytes(b"[donkey\n\xff not toml")

    result = runner.invoke(app, ["--config", str(target), "init", "--force"])

    assert result.exit_code == 0, _streams(result)
    assert "could not be read as TOML" in result.stderr
    assert _table(target)["environment"] == "Sandbox"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_force_keeps_the_file_mode(cwd: Path) -> None:
    target = cwd / ".donkey-kit.toml"
    target.write_text("[donkey]\n", encoding="utf-8")
    target.chmod(0o640)

    result = runner.invoke(app, ["init", "--force"])

    assert result.exit_code == 0, _streams(result)
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert _no_staged_files(cwd)

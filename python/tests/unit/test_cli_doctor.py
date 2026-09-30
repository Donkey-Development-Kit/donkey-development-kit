"""``donkey doctor`` CLI wiring (#202): the report renders, a failing check exits
non-zero (CI-preflight, AC4), ``--json`` is machine-readable, and a missing
``[llm]`` extra is an install prompt (exit 1), not a stack trace.

The probe is monkeypatched (``_live_probe``) so no gateway and no ``[llm]``
extra are needed; runs under ``[dev]`` alone.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from donkey_kit.core.budget import Budget
from donkey_kit.core.errors import AuthError, UpstreamRequestError
from donkey_kit.provisioning import doctor
from donkey_kit.provisioning.cli import app
from donkey_kit.provisioning.doctor import ProbeResult

runner = CliRunner()


def _combined(result: object) -> str:
    text = getattr(result, "stdout", "") or ""
    try:
        text += result.stderr  # type: ignore[attr-defined]
    except (ValueError, AttributeError):
        pass
    return text


@pytest.fixture
def llm_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://gw.example.internal")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "secret")


def _patch_probe(monkeypatch: pytest.MonkeyPatch, result: ProbeResult) -> None:
    monkeypatch.setattr(
        "donkey_kit.provisioning.doctor._live_probe", lambda _c, _m: result
    )


def test_incomplete_config_exits_1_and_names_the_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    for var in ("DONKEY_LLM_PROXY_URL", "DONKEY_LLM_PROXY_CLIENT_ID",
                "DONKEY_LLM_PROXY_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    out = _combined(result)
    assert "[!!] config" in out
    assert "DONKEY_LLM_PROXY_URL" in out


def test_model_rejected_prints_failure_and_exits_1(
    monkeypatch: pytest.MonkeyPatch, llm_env: None
) -> None:
    err = UpstreamRequestError(
        "The upstream model provider rejected the request (400): no such model.",
        code="model_not_found",
        param="model",
    )
    _patch_probe(monkeypatch, ProbeResult(err, None))

    result = runner.invoke(app, ["doctor", "--model", "gpt-4o"])

    assert result.exit_code == 1
    out = _combined(result)
    assert "[!!] model" in out
    assert "[ok] credentials" in out  # got past auth
    assert "request it in API Manager" in out.lower() or "API Manager" in out


def test_wrong_credentials_exits_1(monkeypatch: pytest.MonkeyPatch, llm_env: None) -> None:
    _patch_probe(monkeypatch, ProbeResult(AuthError("nope"), None))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "[!!] credentials" in _combined(result)


def test_all_ok_exits_0(monkeypatch: pytest.MonkeyPatch, llm_env: None) -> None:
    b = Budget()
    b.limit, b.remaining = 20000, 19000
    b.observed_at = doctor._utcnow()
    _patch_probe(monkeypatch, ProbeResult(None, b))

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    out = _combined(result)
    assert "[ok] gateway" in out
    assert "observed" in out


def test_json_output_is_machine_readable(
    monkeypatch: pytest.MonkeyPatch, llm_env: None
) -> None:
    _patch_probe(monkeypatch, ProbeResult(AuthError("nope"), None))
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1  # still non-zero on failure
    payload = json.loads(result.stdout)
    names = {row["name"] for row in payload}
    assert {"config", "gateway", "credentials", "model", "budget"} <= names
    creds = next(r for r in payload if r["name"] == "credentials")
    assert creds["level"] == "fail"
    assert creds["remediation"] == AuthError.remediation


def test_project_url_with_env_credentials_is_reported_without_sending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The real probe path: doctor names the file and host, exits 1 with no
    traceback, and nothing leaves the process."""
    for var in list(os.environ):
        if var.startswith(("ANYPOINT_", "DONKEY_")):
            monkeypatch.delenv(var)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (project / ".donkey-kit.toml").write_text(
        '[donkey]\nllm_proxy_url = "https://llm.example.test/proxy/"\n'
    )
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "secret")

    sent: list[httpx.Request] = []

    def _record(self: object, request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(599)

    async def _arecord(self: object, request: httpx.Request) -> httpx.Response:
        return _record(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _record)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _arecord)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    out = _combined(result)
    assert "[!!] config" in out
    assert "llm.example.test" in out
    assert str(project / ".donkey-kit.toml") in out
    assert "Traceback" not in out
    assert sent == []


def test_project_loopback_url_with_env_credentials_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A loopback proxy URL from the project file gets the same treatment as
    any other host: doctor refuses and the local listener receives nothing."""
    received: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.append(self.path)
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for var in list(os.environ):
            if var.startswith(("ANYPOINT_", "DONKEY_")):
                monkeypatch.delenv(var)
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        port = server.server_address[1]
        (project / ".donkey-kit.toml").write_text(
            f'[donkey]\nllm_proxy_url = "http://127.0.0.1:{port}/proxy/"\n'
        )
        monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
        monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "secret")

        result = runner.invoke(app, ["doctor"])
    finally:
        server.shutdown()
        server.server_close()

    assert result.exit_code == 1
    out = _combined(result)
    assert "[!!] config" in out
    assert str(project / ".donkey-kit.toml") in out
    assert "Traceback" not in out
    assert received == []


def test_missing_llm_extra_prints_pip_install_and_exits_1(
    monkeypatch: pytest.MonkeyPatch, llm_env: None
) -> None:
    def _raise(_model: str, **_: object) -> object:
        raise ImportError("No module named 'openai'")

    monkeypatch.setattr("donkey_kit.provisioning.doctor.run_diagnostics", _raise)
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    out = _combined(result)
    assert 'pip install "donkey-kit[llm]"' in out
    assert "blocked on verification" not in out  # not a verification-discipline gate

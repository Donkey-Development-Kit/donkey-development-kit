"""``scripts/compare_benchmark_trend.py``, the benchmark trend alert from #753.

These tests never touch the network: the earlier-run lookup is injected, and
the one HTTP test captures the request ``urlopen`` would have sent.
"""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.request
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "compare_benchmark_trend.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ddk_compare_benchmark_trend", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trend = _load()

_NAME = "genai_span_overhead_us_per_call"


def _record(value: float, name: str = _NAME, unit: str = "us/call") -> dict[str, Any]:
    return {"name": name, "value": value, "unit": unit}


@pytest.mark.parametrize(
    ("previous", "current", "code", "level"),
    [
        (None, 18.0, 0, "::notice::"),
        (_record(18.0), 20.0, 0, "::notice::"),
        (_record(18.0), 36.0, 1, "::error::"),
        (_record(18.0), 180.0, 1, "::error::"),
        (_record(18.0), 9.0, 0, "::notice::"),
        (_record(0.0), 18.0, 0, "::warning::"),
        (_record(18.0, name="renamed"), 180.0, 0, "::notice::"),
        (_record(18.0, unit="ns/call"), 180.0, 0, "::notice::"),
    ],
)
def test_compare(previous: dict[str, Any] | None, current: float, code: int, level: str) -> None:
    got_code, annotation = trend.compare(_record(current), previous, 2.0)
    assert (got_code, annotation.split("::")[1]) == (code, level.strip(":"))


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fetch: Any,
    *,
    token: str | None = "t",
) -> int:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    if token is not None:
        monkeypatch.setenv("GH_TOKEN", token)
    current = tmp_path / "current.json"
    current.write_text(json.dumps(_record(40.0)))
    argv = ["--current", str(current), "--repo", "o/r", "--workflow", "ci.yml", "--run-id", "7"]
    code: int = trend.main(argv, fetch_previous=fetch)
    return code


def test_main_fails_on_regression_and_excludes_the_current_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, ...]] = []

    def fetch(*args: str) -> dict[str, Any]:
        calls.append(args)
        return _record(10.0)

    assert _run(tmp_path, monkeypatch, fetch) == 1
    assert calls == [("o/r", "ci.yml", "7", "benchmark-result", "t")]
    assert "::error::" in capsys.readouterr().out


def test_main_skips_when_the_api_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fetch(*_: str) -> dict[str, Any]:
        raise OSError("connection refused")

    assert _run(tmp_path, monkeypatch, fetch) == 0
    assert "::warning::" in capsys.readouterr().out


def test_main_skips_without_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fetch(*_: str) -> dict[str, Any]:
        raise AssertionError("must not query the API without a token")

    assert _run(tmp_path, monkeypatch, fetch, token=None) == 0
    assert "::warning::" in capsys.readouterr().out


def test_token_is_not_forwarded_on_the_artifact_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """The artifact download answers 302 to a signed blob-storage URL on another
    host; the GitHub token must not ride along on the redirected request."""
    sent: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> io.BytesIO:
        sent.append(request)
        return io.BytesIO(b"{}")

    monkeypatch.setattr(trend.urllib.request, "urlopen", fake_urlopen)
    trend._open("https://api.github.com/repos/o/r/actions/artifacts/1/zip", "secret-token")

    (request,) = sent
    assert request.get_header("Authorization") == "Bearer secret-token"
    redirected = urllib.request.HTTPRedirectHandler().redirect_request(
        request, io.BytesIO(), 302, "Found", Message(), "https://blob.example/signed?sig=x"
    )
    assert redirected is not None
    assert redirected.get_header("Authorization") is None

"""Base-only tests for the out-of-process ``gateway`` fixture's machinery (#278).

These exercise the request spy and the missing-extra guard **without** booting a
real server, so they run under the base ``[dev]`` install (no ``[local]`` extra):

- ``_record`` redacts the consumer-auth secret and any bearer, and keeps method,
  path and every other header.
- ``_RecordingApp`` records one entry per HTTP request and forwards the ASGI
  call untouched (lifespan/other scopes pass through, unrecorded).
- taking the fixture without the ``[local]`` extra raises an ``ImportError``
  naming the exact ``pip install`` — never a bare ``ModuleNotFoundError``.

The end-to-end path (a real port, a stock client, ``set_scenarios``) lives in
``tests/conformance/test_gateway_fixture.py`` behind the ``local_gateway`` marker.
"""

from __future__ import annotations

import builtins
from typing import Any

import pytest

from donkey_kit.conformance.gateway import (
    RecordedRequest,
    _record,
    _RecordingApp,
    start_gateway,
)


def _http_scope(
    headers: list[tuple[bytes, bytes]], *, method: str = "POST", path: str = "/responses"
) -> dict[str, Any]:
    return {"type": "http", "method": method, "path": path, "headers": headers}


def test_record_redacts_secret_and_bearer_keeps_the_rest() -> None:
    req = _record(
        _http_scope(
            [
                (b"client_id", b"public-abc"),
                (b"client_secret", b"super-secret-value"),
                (b"authorization", b"Bearer tok"),
                (b"content-type", b"application/json"),
            ]
        )
    )
    assert req.method == "POST"
    assert req.path == "/responses"
    # The public id and content-type survive verbatim; only the secret and bearer
    # are redacted — so a captured request is safe to print in a failure message.
    assert req.headers["client_id"] == "public-abc"
    assert req.headers["content-type"] == "application/json"
    assert req.headers["client_secret"] == "***"
    assert req.headers["authorization"] == "***"


def test_record_redaction_is_case_insensitive() -> None:
    # Both case variants of the real `client_secret` request header are redacted;
    # the name is matched case-insensitively (ASGI lowercases names, but a proxy
    # or client could send any case).
    req = _record(_http_scope([(b"Client_Secret", b"nope"), (b"CLIENT_SECRET", b"nope")]))
    assert set(req.headers.values()) == {"***"}


async def test_recording_app_records_http_and_forwards() -> None:
    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    async def receive() -> dict[str, Any]:  # pragma: no cover - not driven here
        return {}

    async def send(message: dict[str, Any]) -> None:  # pragma: no cover
        return None

    app = _RecordingApp(inner)
    await app(_http_scope([(b"client_secret", b"s")], method="GET", path="/models"), receive, send)
    await app({"type": "lifespan"}, receive, send)

    # Both scopes reach the inner app; only the HTTP one is recorded.
    assert seen == ["http", "lifespan"]
    assert len(app.requests) == 1
    (only,) = app.requests
    assert isinstance(only, RecordedRequest)
    assert only.method == "GET"
    assert only.path == "/models"
    assert only.headers["client_secret"] == "***"


def test_missing_local_extra_names_the_pip_install(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def _no_uvicorn(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "uvicorn" or name.startswith("uvicorn."):
            raise ImportError("No module named 'uvicorn'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_uvicorn)
    with pytest.raises(ImportError, match=r'pip install "donkey-kit\[local\]"'):
        start_gateway()

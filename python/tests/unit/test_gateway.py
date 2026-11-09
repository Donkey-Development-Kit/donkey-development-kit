"""Base-only tests for the out-of-process ``gateway`` fixture's machinery (#278).

These exercise the request spy and the missing-extra guard **without** booting a
real server, so they run under the base install plus the ``dev`` group (no ``[local]`` extra):

- ``_record`` keeps method, path and allowlisted attribution/content-type values,
  redacting every other header value.
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
from donkey_kit.core.config import DonkeyConfig


def _http_scope(
    headers: list[tuple[bytes, bytes]], *, method: str = "POST", path: str = "/responses"
) -> dict[str, Any]:
    return {"type": "http", "method": method, "path": path, "headers": headers}


def test_record_redacts_secret_and_bearer_keeps_content_type_and_client_id() -> None:
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


@pytest.mark.parametrize(
    "name", [b"X-Custom-Key", b"X-User-Token", b"Cookie", b"Proxy-Authorization", b"X-Unknown"]
)
def test_record_redacts_headers_not_explicitly_safe(name: bytes) -> None:
    req = _record(_http_scope([(name, b"synthetic-sensitive-value")]))
    assert req.headers[name.decode("latin-1")] == "***"
    assert "synthetic-sensitive-value" not in repr(req)


def test_record_keeps_credential_reserved_attribution_only() -> None:
    headers = [
        (b"client_id", b"consumer"),
        (b"x-client-id", b"wallet"),
        (b"x-correlation-id", b"run"),
        (b"x-donkey-request-id", b"call"),
        (b"x-anypoint-cost-team", b"team"),
    ]
    req = _record(_http_scope(headers))
    assert req.headers["client_id"] == "consumer"
    assert req.headers["x-client-id"] == "wallet"
    for name in ("x-correlation-id", "x-donkey-request-id", "x-anypoint-cost-team"):
        assert req.headers[name] == "***"


@pytest.mark.parametrize("field", ["llm_proxy_key_header", "user_token_header"])
@pytest.mark.parametrize(
    ("attribution_field", "header"),
    [
        ("correlation_header", "X-Correlation-Id"),
        ("call_id_header", "X-Donkey-Request-Id"),
        ("cost_team_header", "X-Anypoint-Cost-Team"),
        ("cost_project_header", "X-Anypoint-Cost-Project"),
        ("cost_env_header", "X-Anypoint-Cost-Env"),
        ("cost_enduser_header", "X-Anypoint-Cost-Enduser-Id"),
    ],
)
def test_repurposed_attribution_is_redacted_with_or_without_config(
    field: str, attribution_field: str, header: str
) -> None:
    cfg = DonkeyConfig(**{attribution_field: "X-Moved", field: header})
    scope = _http_scope([(header.encode(), b"secret")])
    for recording_config in (None, cfg):
        req = _record(scope, cfg=recording_config)
        assert req.headers[header] == "***"


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

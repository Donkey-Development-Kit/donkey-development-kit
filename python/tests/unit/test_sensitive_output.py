"""Secrets, blocked content and cost tags stay out of rendered output unless the
caller asks for them.

Three places render values a developer never meant to print:

* an object's ``repr``/``str`` — tracebacks with locals, pytest diffs, log lines;
* a refusal's message — ``str(exc)``, ``exc.args``, ``donkey doctor``;
* request headers nothing on the gateway reads — the cost-tag headers
  (docs/verified-apis.md §3, #522), whose only consumer is the ``donkey.cost.*``
  span attribute.

Each test names the value that must not appear and asserts on the rendered text.
The PII cases are driven by the live capture
``src/donkey_kit/simulator/_fixtures/anypoint/llm_proxy/reject.pii-detected.*``
(docs/verified-apis.md §4).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from donkey_kit.cli import app, doctor
from donkey_kit.cli.doctor import ProbeResult, format_report, run_diagnostics
from donkey_kit.core import _verify, telemetry
from donkey_kit.core.auth import AnypointConnectedApp, ChainedAuth, StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.cost import CostTags
from donkey_kit.core.errors import PIIDetected, PolicyViolation, classify
from donkey_kit.core.telemetry import run_scope
from donkey_kit.core.transport import (
    DonkeyAsyncClient,
    DonkeyClient,
    attribution_headers,
    proxy_auth_headers,
)
from donkey_kit.integrations.langgraph import LangGraphAdapter
from donkey_kit.llm.client import LLMClient
from donkey_kit.simulator.fixtures import parse_headers

LLM_PROXY = (
    Path(__file__).resolve().parents[2] / "src" / "donkey_kit" / "simulator" / "_fixtures"
    / "anypoint"
    / "llm_proxy"
)


def _isolate_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str | None = None) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    if body is not None:
        (tmp_path / ".donkey-kit.toml").write_text(body)


# --- object reprs ---------------------------------------------------------------

_SECRETS = {
    "client_secret": "cp-secret-value",
    "llm_proxy_client_secret": "proxy-secret-value",
    "llm_proxy_key": "proxy-key-value",
}


def _secret_cfg() -> DonkeyConfig:
    return DonkeyConfig(
        client_id="cp-client-id",
        llm_proxy_url="https://proxy.example.internal",
        llm_proxy_client_id="proxy-client-id",
        **_SECRETS,
    )


@pytest.mark.parametrize("render", [repr, str], ids=["repr", "str"])
def test_config_rendering_omits_secret_values(render: Callable[[object], str]) -> None:
    text = render(_secret_cfg())
    for name, value in _SECRETS.items():
        assert value not in text, f"{name} rendered by {render.__name__}()"


def test_config_rendering_keeps_non_secret_fields_for_debugging() -> None:
    text = repr(_secret_cfg())
    assert "client_id='cp-client-id'" in text
    assert "llm_proxy_client_id='proxy-client-id'" in text
    assert "llm_proxy_url='https://proxy.example.internal'" in text


def test_config_secret_fields_stay_readable_and_comparable() -> None:
    cfg = _secret_cfg()
    assert cfg.llm_proxy_client_secret == "proxy-secret-value"
    assert cfg != cfg.with_overrides(llm_proxy_client_secret="other-secret")


def _credential_holders(cfg: DonkeyConfig) -> list[object]:
    """Every public object built from a credential, constructed without I/O."""
    http = DonkeyAsyncClient(cfg, StaticToken("static-token-value"))
    return [
        StaticToken("static-token-value"),
        AnypointConnectedApp(
            client_id="cp-client-id",
            client_secret="cp-secret-value",
            control_plane_url="https://anypoint.example.internal",
            http_client=httpx.AsyncClient(),
        ),
        ChainedAuth(StaticToken("static-token-value")),
        http,
        DonkeyClient(cfg),
        LLMClient(cfg, http),
        LangGraphAdapter(cfg, http),
    ]


def test_credential_holders_render_no_secret_values() -> None:
    cfg = _secret_cfg()
    for obj in _credential_holders(cfg):
        for render in (repr, str):
            text = render(obj)
            for value in (*_SECRETS.values(), "static-token-value"):
                assert value not in text, f"{type(obj).__name__} rendered {value!r}"


def test_donkey_renders_no_secret_values() -> None:
    from donkey_kit.donkey import Donkey

    donkey = Donkey(_secret_cfg().with_overrides(telemetry=False))
    try:
        for render in (repr, str):
            text = render(donkey)
            for value in _SECRETS.values():
                assert value not in text
    finally:
        donkey.close()


# --- PII refusal messages ----------------------------------------------------------

#: The entity value inside the live capture's gateway message.
_PII_VALUE = "john.doe@example.com"


def _pii_body() -> dict[str, object]:
    body = json.loads((LLM_PROXY / "reject.pii-detected.body.json").read_text())
    assert isinstance(body, dict)
    return body


def _pii_refusal() -> PIIDetected:
    headers = parse_headers((LLM_PROXY / "reject.pii-detected.headers.txt").read_text())
    err = classify(httpx.Response(403, headers=headers, json=_pii_body()))
    assert isinstance(err, PIIDetected)
    return err


def _pii_refusal_with_message(message: str) -> PIIDetected:
    body = {"error": {"message": message, "type": "pii_detected"}}
    err = classify(httpx.Response(403, json=body))
    assert isinstance(err, PIIDetected)
    return err


def test_live_pii_capture_carries_the_entity_value() -> None:
    # Precondition for the tests below: the gateway echoes the value it blocked.
    assert _PII_VALUE in json.dumps(_pii_body())


def test_pii_refusal_str_repr_and_args_omit_detected_values() -> None:
    err = _pii_refusal()
    assert _PII_VALUE not in str(err)
    assert _PII_VALUE not in repr(err)
    assert all(_PII_VALUE not in str(arg) for arg in err.args)


def test_pii_refusal_message_names_entity_types_and_offsets() -> None:
    message = str(_pii_refusal())
    assert "Email" in message
    assert "12-32" in message  # the capture's start/end character offsets


def test_pii_refusal_keeps_gateway_message_on_explicit_attribute() -> None:
    err = _pii_refusal()
    error = _pii_body()["error"]
    assert isinstance(error, dict)
    assert err.gateway_message == error["message"]
    assert err.response is not None
    assert err.response.json() == _pii_body()


def test_pii_refusal_still_lists_entity_types() -> None:
    assert _pii_refusal().entities == ["Email"]


def test_pii_refusal_with_several_entities_counts_them_without_values() -> None:
    # The captured message shape, with a second entity appended.
    message = (
        'Request contains PII data: [\n  {\n    "pii_type": "Email",\n'
        '    "value": "a.person@example.com",\n    "start": 3,\n    "end": 23\n  },\n'
        '  {\n    "pii_type": "Phone",\n    "value": "+1 555 0100",\n'
        '    "start": 30,\n    "end": 41\n  }\n]'
    )
    err = _pii_refusal_with_message(message)
    text = str(err)
    assert "a.person@example.com" not in text
    assert "+1 555 0100" not in text
    assert "2 entities" in text
    assert "Email" in text and "Phone" in text
    assert err.entities == ["Email", "Phone"]


def test_pii_refusal_with_unparseable_entity_list_omits_values() -> None:
    message = 'Request contains PII data: [{"pii_type": "Email", "value": "cut@exam'
    err = _pii_refusal_with_message(message)
    assert "cut@exam" not in str(err)
    assert "Email" in str(err)
    assert err.gateway_message == message


def test_unrecognised_refusal_does_not_render_its_body() -> None:
    body = {"error": "blocked", "echo": "my account number is 12345678"}
    err = classify(httpx.Response(403, json=body))
    assert type(err) is PolicyViolation
    assert "12345678" not in str(err)
    assert "12345678" not in repr(err)


# --- donkey doctor -------------------------------------------------------------------


@pytest.fixture
def llm_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://gw.example.internal")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "secret")


def test_doctor_report_omits_blocked_values(llm_env: None) -> None:
    result = ProbeResult(_pii_refusal(), None)
    checks = run_diagnostics("gpt-4o", probe=lambda _cfg, _model: result)
    report = format_report(checks)
    assert "policy" in report
    assert _PII_VALUE not in report


@pytest.mark.parametrize("args", [["doctor"], ["doctor", "--json"]], ids=["text", "json"])
def test_doctor_cli_omits_blocked_values(
    monkeypatch: pytest.MonkeyPatch, llm_env: None, args: list[str]
) -> None:
    result = ProbeResult(_pii_refusal(), None)
    monkeypatch.setattr(doctor, "_live_probe", lambda _cfg, _model: result)
    out = CliRunner().invoke(app, args)
    assert "Email" in out.output
    assert _PII_VALUE not in out.output


# --- cost-tag request headers ----------------------------------------------------------

_COST = CostTags(team="support", project="triage-v2", env="prod", enduser_id="user-42")
_COST_HEADER_NAMES = {
    name.lower()
    for name in (
        _verify.COST_TEAM_HEADER,
        _verify.COST_PROJECT_HEADER,
        _verify.COST_ENV_HEADER,
        _verify.COST_ENDUSER_HEADER,
    )
}


def _cost_headers_in(headers: dict[str, str]) -> set[str]:
    return {name.lower() for name in headers} & _COST_HEADER_NAMES


def _recording_handler(seen: dict[str, str]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    return handler


async def test_default_config_sends_no_cost_headers() -> None:
    seen: dict[str, str] = {}
    cfg = DonkeyConfig(cost=_COST)
    async with DonkeyAsyncClient(
        cfg, None, transport=httpx.MockTransport(_recording_handler(seen))
    ) as client:
        await client.get("https://proxy/thing")
    assert _cost_headers_in(seen) == set()
    assert "user-42" not in seen.values()


async def test_default_config_sends_no_run_scope_cost_headers() -> None:
    seen: dict[str, str] = {}
    async with DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(_recording_handler(seen))
    ) as client:
        with run_scope("run-1", CostTags(enduser_id="run-user")):
            await client.get("https://proxy/thing")
    assert _cost_headers_in(seen) == set()
    assert "run-user" not in seen.values()


def test_default_sync_client_sends_no_cost_headers() -> None:
    seen: dict[str, str] = {}
    cfg = DonkeyConfig(cost=_COST)
    with DonkeyClient(cfg, transport=httpx.MockTransport(_recording_handler(seen))) as client:
        client.get("https://proxy/thing")
    assert _cost_headers_in(seen) == set()


def test_default_header_snapshots_carry_no_cost_tags() -> None:
    cfg = DonkeyConfig(cost=_COST, llm_proxy_client_id="cid", llm_proxy_client_secret="csecret")
    assert _cost_headers_in(attribution_headers(cfg)) == set()
    assert _cost_headers_in(proxy_auth_headers(cfg)) == set()


async def test_opted_in_config_sends_config_and_run_scope_cost_headers() -> None:
    seen: dict[str, str] = {}
    cfg = DonkeyConfig(cost=CostTags(team="support"), send_cost_headers=True)
    async with DonkeyAsyncClient(
        cfg, None, transport=httpx.MockTransport(_recording_handler(seen))
    ) as client:
        with run_scope("run-1", CostTags(enduser_id="run-user")):
            await client.get("https://proxy/thing")
    assert seen[_verify.COST_TEAM_HEADER.lower()] == "support"
    assert seen[_verify.COST_ENDUSER_HEADER.lower()] == "run-user"


def test_opted_in_header_snapshot_carries_cost_tags() -> None:
    cfg = DonkeyConfig(cost=_COST, send_cost_headers=True)
    assert _cost_headers_in(attribution_headers(cfg)) == _COST_HEADER_NAMES


async def test_cost_tags_reach_span_attributes_without_cost_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("donkey_kit.test")
    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-llm-proxy-llm-provider": "openai"},
            json={"model": "gpt-4o", "usage": {"input_tokens": 1, "output_tokens": 1}},
        )

    cfg = DonkeyConfig(llm_proxy_url="https://proxy", cost=_COST)
    async with DonkeyAsyncClient(cfg, None, transport=httpx.MockTransport(handler)) as client:
        await client.post("https://proxy/chat", json={"model": "gpt-4o"})

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes or {})
    assert attrs["donkey.cost.team"] == "support"
    assert attrs["donkey.cost.enduser.id"] == "user-42"


def test_send_cost_headers_defaults_to_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    monkeypatch.delenv("DONKEY_SEND_COST_HEADERS", raising=False)
    assert DonkeyConfig().send_cost_headers is False
    assert DonkeyConfig.from_env().send_cost_headers is False


def test_send_cost_headers_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    monkeypatch.setenv("DONKEY_SEND_COST_HEADERS", "true")
    assert DonkeyConfig.from_env().send_cost_headers is True


def test_send_cost_headers_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch, "[donkey]\nsend_cost_headers = true\n")
    monkeypatch.delenv("DONKEY_SEND_COST_HEADERS", raising=False)
    assert DonkeyConfig.from_env().send_cost_headers is True

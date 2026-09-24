"""The shared fixture loader (BG §1.4): parser parity with the classify()
contract, status resolution per shape, and byte-identity of what it serves.

Unguarded — the loader needs no web framework, so this runs in the base-only CI
job under ``[dev]`` only (the same guarantee [[test_simulator_base_only]] pins).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from donkey_kit.simulator import fixtures as fx

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# shape -> the status classify() is contract-tested against for that shape. The
# simulator MUST reproduce these exactly, or a stock client sees a different
# status than the SDK's typed refusals were verified against.
_CONTRACT_STATUS = {
    "token-rate-limit": 429,
    "pii-detected": 403,
    "injection-protection": 400,
    "regex-prompt-guard": 403,
    "content-safety": 403,
    "content-moderation": 400,
    "model-not-found": 400,
    "upstream-5xx": 503,
    "client-id-missing": 401,
    "success": 200,
    "success-semantic": 200,
    "stream": 200,
    "models-notfound": 404,
}


def test_parse_headers_matches_the_documented_rule() -> None:
    text = (
        "HTTP/1.1 403 Forbidden\n"
        "content-type: application/json\n"
        "X-Foo: Bar\n"
        "garbage-no-colon\n"
    )
    # HTTP line + colon-less line skipped; key lowercased; value stripped.
    assert fx.parse_headers(text) == {"content-type": "application/json", "x-foo": "Bar"}


def test_parse_headers_keeps_only_the_first_colon_split() -> None:
    # www-authenticate values and timestamps contain colons — only the first splits.
    assert fx.parse_headers("X-When: 12:30:00\n") == {"x-when": "12:30:00"}


def test_parse_status_reads_the_http_line() -> None:
    assert fx.parse_status("HTTP/1.1 429 Too Many Requests\nx: y\n") == 429
    assert fx.parse_status("no status line here\nx: y\n") is None


@pytest.mark.parametrize("shape", sorted(_CONTRACT_STATUS))
def test_status_matches_the_classify_contract(shape: str) -> None:
    assert fx.load(shape).status == _CONTRACT_STATUS[shape]


def test_shape_table_covers_exactly_the_contract_shapes() -> None:
    # A new shape added to SHAPES without a documented status (or vice versa) is
    # a drift this catches before it reaches the app.
    assert set(fx.SHAPES) == set(_CONTRACT_STATUS)


def test_model_not_found_uses_the_400_fallback_with_no_header_file() -> None:
    # model-not-found was captured body-only (no .headers.txt), matching
    # test_row5's httpx.Response(400, json=body). The loader must fall back to
    # 400 + application/json rather than invent a header block.
    f = fx.load("model-not-found")
    assert f.status == 400
    assert f.content_type == "application/json"
    assert f.headers == {}
    assert b"model_not_found" in f.body


def test_empty_body_shapes_load_as_empty_bytes() -> None:
    # Rows 1/3/4/6 are real empty-body captures — not a guessed body.
    for shape in ("token-rate-limit", "injection-protection", "content-moderation", "upstream-5xx"):
        assert fx.load(shape).body == b""


def test_every_shape_body_matches_the_source_file_bytes() -> None:
    """BG §1.4 "same files, both fail together": every shape the simulator serves
    resolves to the byte-identical file the classify() contract reads. If a
    fixture body drifts, this and the contract test fail together."""
    for shape, spec in fx.SHAPES.items():
        served = fx.load(shape).body
        if spec.body is None:
            assert served == b"", shape
        else:
            source = (_FIXTURES / spec.directory / spec.body).read_bytes()
            assert served == source, shape


def test_every_header_shape_matches_the_source_headers() -> None:
    for shape, spec in fx.SHAPES.items():
        if spec.headers is None:
            continue
        source_text = (_FIXTURES / spec.directory / spec.headers).read_text()
        assert fx.load(shape).headers == fx.parse_headers(source_text), shape


def test_unknown_shape_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        fx.load("no-such-shape")


def test_success_carries_the_gateway_routing_headers() -> None:
    # These x-llm-proxy-* headers are what the app replays (allow-list) so the
    # happy path looks like the real proxy's attribution surface.
    h = fx.load("success").headers
    assert h["x-llm-proxy-llm-provider"] == "openai"
    assert h.get("content-type", "").startswith("application/json")

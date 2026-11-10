"""Offline packaged MCP evidence and matcher boundaries (BG section 1.4 / 2.2, #1391)."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path

import httpx
import pytest

from donkey_kit import PIIDetected, PolicyViolation, classify, jsonrpc_refusal
from donkey_kit.core.tool_access import ToolNotInProfile, tool_access_denial
from donkey_kit.simulator import fixtures

_ORIGINALS = Path(__file__).resolve().parents[1] / "fixtures"
_POLICIES = (
    "abac-denied",
    "mapping-call",
    "mapping-list",
    "optimization-html",
    "pii-mask-sse",
    "pii-request",
    "pii-response-sse",
    "poisoning-block-list",
    "poisoning-remove-list",
    "progressive-invalid-params",
    "progressive-list",
    "schema-invalid-args",
    "schema-missing-id",
)
_ACCESS = ("abac", "global-access")
_LOCK = json.loads(fixtures.fixture_bytes("anypoint", "mcp-captures.lock"))


def _fixture(name: str, *, access: bool = False) -> fixtures.Fixture:
    if access:
        directory = "anypoint/mcp_access"
        prefix = f"tools-call.{name}-denied"
        raw = fixtures.fixture_bytes(directory, f"{prefix}.headers.txt").decode("utf-8")
        status = fixtures.parse_status(raw)
        assert status is not None
        headers = fixtures.parse_headers(raw)
        body = fixtures.fixture_bytes(directory, f"{prefix}.body.json")
    else:
        capture = json.loads(fixtures.fixture_bytes("anypoint/mcp_policies", f"{name}.json"))
        status, headers = capture["status"], capture["headers"]
        body = capture["body"].encode("utf-8")
    return fixtures.Fixture(f"mcp-capture:{name}", status, headers, body, headers["content-type"])


def _replay(fixture: fixtures.Fixture) -> httpx.Response:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            fixture.status,
            headers={
                **fixtures.replay_headers(fixture),
                "content-type": fixture.content_type or "",
                "x-donkey-simulator": "true",
            },
            content=fixture.body,
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        return client.post("https://gateway.example/mcp/")


def _rpc(response: httpx.Response) -> dict:
    body = response.content
    if response.headers["content-type"] == "text/event-stream":
        assert body.startswith(b"event: message\ndata: ") and body.endswith(b"\n\n")
        body = body.removeprefix(b"event: message\ndata: ").removesuffix(b"\n\n")
    return json.loads(body)


@pytest.mark.parametrize("path", sorted(_LOCK))
def test_packaged_bytes_and_provenance_match_originals_and_hashes(path: str) -> None:
    """Copied captures are immutable; #1384 adds test-only provenance after packaging."""
    directory, name = path.rsplit("/", 1)
    packaged = fixtures.fixture_bytes(directory, name)
    assert "sha256:" + hashlib.sha256(packaged).hexdigest() == _LOCK[path]
    if path == "anypoint/mcp_policies/README.md":
        # The packaged README describes only the historical 2026-11-02 capture set.
        assert b"Captured 2026-11-02" in packaged
        assert b"pii-response-json" not in packaged
        return
    assert packaged == (_ORIGINALS / path).read_bytes()
    original_lock = json.loads((_ORIGINALS / "fixtures.lock").read_bytes())
    assert _LOCK[path] == original_lock[path]


def test_manifest_covers_exactly_the_copied_tree_without_served_shapes() -> None:
    """Resource traversal catches unlocked additions; the served lock stays disjoint."""
    root = files("donkey_kit.simulator") / "_fixtures" / "anypoint"
    actual = {
        f"anypoint/{directory}/{resource.name}"
        for directory in ("mcp_policies", "mcp_access")
        for resource in (root / directory).iterdir()
        if resource.is_file()
    }
    expected = {f"anypoint/mcp_policies/{name}.json" for name in _POLICIES}
    expected |= {
        f"anypoint/mcp_access/tools-call.{name}-denied.{suffix}"
        for name in _ACCESS
        for suffix in ("headers.txt", "body.json")
    }
    expected |= {f"anypoint/{directory}/README.md" for directory in ("mcp_policies", "mcp_access")}
    assert actual == set(_LOCK) == expected
    assert set(_LOCK).isdisjoint(fixtures.read_lock())
    assert not any(
        spec.directory in ("anypoint/mcp_policies", "anypoint/mcp_access")
        for spec in fixtures.SHAPES.values()
    )
    assert fixtures.fixture_bytes("anypoint", "README.mcp.md")
    assert not (root / "mcp_policies" / "pii-response-json.json").is_file()
    assert not (root / "mcp_policies" / "pii-response-json.provenance.json").is_file()


@pytest.mark.parametrize("path", sorted(_LOCK))
def test_integrity_detects_even_trailing_whitespace(path: str) -> None:
    """A benign in-memory mutation must fail the capture digest without editing evidence."""
    directory, name = path.rsplit("/", 1)
    mutated = fixtures.fixture_bytes(directory, name) + b" "
    assert "sha256:" + hashlib.sha256(mutated).hexdigest() != _LOCK[path]


@pytest.mark.parametrize(
    "name,access", [(name, False) for name in _POLICIES] + [(name, True) for name in _ACCESS]
)
def test_raw_mock_transport_replays_exact_status_body_and_content_type(
    name: str, access: bool
) -> None:
    """All 15 responses replay packaged wire bytes, not reserialized JSON or an LLM shape."""
    fixture = _fixture(name, access=access)
    before = dict(fixture.headers)
    response = _replay(fixture)
    assert response.status_code == fixture.status
    assert response.content == fixture.body
    assert response.headers["content-type"] == fixture.content_type
    assert response.headers["x-donkey-simulator"] == "true"
    assert "content-length" not in fixtures.replay_headers(fixture)
    assert fixture.headers == before and "x-donkey-simulator" not in fixture.headers
    assert _rpc(response)["jsonrpc"] == "2.0"


@pytest.mark.parametrize(
    "name,phase", [("pii-request", "request"), ("pii-response-sse", "response")]
)
def test_packaged_pii_matches_existing_rpc_and_http_matchers(name: str, phase: str) -> None:
    """Ledger MCP rows: response-sse is HTTP 403 JSON, not an SSE refusal event."""
    response = _replay(_fixture(name))
    assert response.status_code == 403 and response.headers["content-type"] == "application/json"
    for result in (jsonrpc_refusal(_rpc(response), response=response), classify(response)):
        assert type(result) is PIIDetected
        assert isinstance(result, PolicyViolation) and result.remediation
        assert result.policy == "mcp-pii-detector" and result.phase == phase
        assert result.response is response
        assert result.findings and all(
            set(f) == {"pii_type", "masked_value"} for f in result.findings
        )


@pytest.mark.parametrize(
    "name,access,tool",
    [
        ("abac-denied", False, "calculate"),
        ("abac", True, "lookup_proxy"),
        ("global-access", True, "current_time"),
    ],
)
def test_packaged_access_denials_match_existing_matchers(
    name: str, access: bool, tool: str
) -> None:
    """#303/#1384: HTTP 200 access refusals are recognized explicitly through RPC."""
    response = _replay(_fixture(name, access=access))
    body = _rpc(response)
    assert response.status_code == 200
    for result in (jsonrpc_refusal(body, response=response), tool_access_denial(body)):
        assert type(result) is ToolNotInProfile
        assert result.tool_name == tool and result.remediation


@pytest.mark.parametrize(
    "name",
    [name for name in _POLICIES if name not in ("pii-request", "pii-response-sse", "abac-denied")],
)
def test_ambiguous_errors_and_transformed_results_do_not_match_a_policy(name: str) -> None:
    """#1401: schema/poisoning have no safe policy identity; isError is not a refusal."""
    response = _replay(_fixture(name))
    body = _rpc(response)
    assert jsonrpc_refusal(body, response=response) is None
    assert tool_access_denial(body) is None
    if name == "optimization-html":
        assert body["result"]["isError"] is True and "error" not in body
        assert jsonrpc_refusal(body["result"]) is None
    if name in (
        "schema-invalid-args",
        "schema-missing-id",
        "poisoning-block-list",
        "progressive-invalid-params",
    ):
        assert "error" in body and "policy" not in body["error"]["data"]

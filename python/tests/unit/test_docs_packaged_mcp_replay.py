"""Consumer MCP replay example and boundaries (BG section 1.4 / 2.2, #1392)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from donkey_kit import PIIDetected
from donkey_kit.simulator.fixtures import fixture_bytes

_CONTENT = Path(__file__).resolve().parents[3] / "website" / "content"
_PAGE = _CONTENT / "simulator.mdx"
_HEADING = "## Packaged MCP captures"
_LINK = "/simulator#packaged-mcp-captures"

pytestmark = pytest.mark.skipif(
    not _CONTENT.is_dir(), reason="the repository docs are not included in the sdist"
)


@pytest.mark.disable_socket
def test_packaged_mcp_docs_example_replays_bytes_offline() -> None:
    """Execute the actual fence, with all sockets disabled and no optional framework."""
    text = _PAGE.read_text(encoding="utf-8")
    section = text.split(_HEADING, 1)[1].split("\n## ", 1)[0]
    fences = re.findall(r"```python\n(.*?)```", section, re.DOTALL)
    assert len(fences) == 1
    original = fixture_bytes("anypoint/mcp_policies", "pii-request.json")
    capture = json.loads(original)
    namespace: dict[str, Any] = {}
    exec(compile(fences[0], str(_PAGE), "exec"), namespace)

    response = namespace["response"]
    assert response.status_code == capture["status"] == 403
    assert response.content == capture["body"].encode("utf-8")
    assert response.headers["content-type"] == "application/json"
    assert response.headers["x-donkey-simulator"] == "true"
    assert str(response.request.url) == "https://gateway.example/mcp/"
    refusal = namespace["refusal"]
    assert type(refusal) is PIIDetected
    assert refusal.response is response and refusal.phase == "request"
    assert namespace["fixture"].headers == capture["headers"]
    assert fixture_bytes("anypoint/mcp_policies", "pii-request.json") == original


def test_packaged_mcp_docs_preserve_replay_boundaries() -> None:
    """Packaging is not MCP session simulation, policy inference, or new evidence."""
    text = _PAGE.read_text(encoding="utf-8")
    section = " ".join(text.split(_HEADING, 1)[1].split("\n## ", 1)[0].split())
    for boundary in (
        "15 captured MCP responses",
        "13 policy envelopes",
        "two access-denial header/body pairs",
        "available on `develop`, not yet in a released package",
        "not a `donkey mock` route or a `donkey-sim/` sentinel",
        "not bound-tool `simulate()` injection",
        "no session negotiation or request-ID rewriting",
        "not JSON-reserialized",
        "reserved example address, not a real endpoint",
        "Schema and poisoning errors cannot safely identify a gateway policy",
        "`pii-response-sse.json` is HTTP 403 `application/json`",
        "not an SSE error event",
    ):
        assert boundary in section, boundary


def test_packaged_mcp_replay_is_linked_without_duplicate_examples() -> None:
    for relative in (
        "tool-access/gateway-policies.mdx",
        "examples/general/simulating-refusals.mdx",
    ):
        text = (_CONTENT / relative).read_text(encoding="utf-8")
        assert _LINK in text, relative
        assert "fixture_bytes(" not in text, relative

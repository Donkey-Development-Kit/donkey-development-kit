"""Selected live Agent Card rewrite contract (BG section 2.9, #1393)."""

from __future__ import annotations

import json
from pathlib import Path


def test_captured_card_rewrites_urls_and_preserves_interface_fields() -> None:
    """Ledger section 4: explicit consumerUrl rewrites both JSONRPC versions."""
    path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "anypoint"
        / "a2a_policies"
        / "v1-card-rewrite.selected.json"
    )
    capture = json.loads(path.read_text())
    upstream = capture["upstream"]
    gateway = capture["gateway"]
    assert upstream["status"] == gateway["status"] == 200
    assert upstream["contentType"] == gateway["contentType"] == "application/json"
    original = upstream["supportedInterfaces"]
    rewritten = gateway["supportedInterfaces"]
    assert len(original) == len(rewritten) == 2
    assert [item["protocolVersion"] for item in rewritten] == ["1.0", "0.3.0"]
    for before, after in zip(original, rewritten, strict=True):
        assert before["url"] != after["url"]
        assert after["url"] == capture["consumerUrl"]
        assert {key: value for key, value in before.items() if key != "url"} == {
            key: value for key, value in after.items() if key != "url"
        }

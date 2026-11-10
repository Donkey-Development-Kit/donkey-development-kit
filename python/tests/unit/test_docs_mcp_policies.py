"""MCP policy docs preserve the evidence boundary (BG §2.7, BG §1.2, #1392)."""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_CONTENT = _ROOT / "website" / "content"
_ROUTE = "/tool-access/gateway-policies"

pytestmark = pytest.mark.skipif(
    not _CONTENT.is_dir(), reason="the repository docs are not included in the sdist"
)


def test_mcp_policy_page_is_navigable_from_affected_surfaces() -> None:
    page = _CONTENT / "tool-access" / "gateway-policies.mdx"
    assert page.is_file()
    nav = (_CONTENT / "tool-access" / "_meta.js").read_text(encoding="utf-8")
    assert "'gateway-policies': 'MCP gateway policies'" in nav
    for relative in (
        "tool-access/discovery.mdx",
        "tool-access/binding.mdx",
        "tool-access/lockfile.mdx",
        "errors.mdx",
    ):
        assert _ROUTE in (_CONTENT / relative).read_text(encoding="utf-8"), relative


def test_mcp_policy_inventory_keeps_partial_evidence_explicit() -> None:
    text = (_CONTENT / "tool-access" / "gateway-policies.mdx").read_text(encoding="utf-8")
    headings = (
        "## MCP Support",
        "## MCP Global Access",
        "## MCP Attribute-Based Access Control",
        "## MCP PII Detector",
        "## MCP Schema Validation and Tool Poisoning",
        "## MCP Tools Progressive Disclosure",
        "## MCP Tool Mapping",
        "## MCP Payload Optimization",
        "## Agent telemetry",
        "## MCP Bridge",
    )
    positions = [text.index(heading) for heading in headings]
    assert positions == sorted(positions)
    for boundary in (
        "HTTP 403 JSON",
        "not an SSE error event",
        "local capture replay",
        "expected_info",
        "no TOON decoder",
        "mcp-transcoding",
        "source-only",
        "not live-verified",
        "no capture-backed fixture tests",
        "pins this gateway-visible catalog",
    ):
        assert boundary in text, boundary

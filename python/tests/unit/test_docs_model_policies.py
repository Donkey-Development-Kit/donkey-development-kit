"""The Model gateway policies page names every typed model refusal (BG §1.5, #1829)."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import donkey_kit
from donkey_kit.core import errors

_ROOT = Path(__file__).resolve().parents[3]
_CONTENT = _ROOT / "website" / "content"
_PAGE = _CONTENT / "governance" / "gateway-policies" / "models.mdx"
_ROUTE = "/governance/gateway-policies/models"

pytestmark = pytest.mark.skipif(
    not _CONTENT.is_dir(), reason="the repository docs are not included in the sdist"
)

#: Typed HTTP refusals of a model call that are not ``PolicyViolation``
#: subclasses defined in ``core/errors.py``.
_OTHER_MODEL_REFUSALS = (
    "AuthError",
    "GatewayApprovalPending",
    "GatewayApprovalDenied",
    "ModelNotRoutable",
    "UpstreamModelError",
)


def _model_refusals() -> set[str]:
    """Every ``PolicyViolation`` that ``core/errors.py`` itself defines, plus the rest."""
    defined = {
        name
        for name, obj in vars(errors).items()
        if inspect.isclass(obj)
        and issubclass(obj, errors.PolicyViolation)
        and obj.__module__ == errors.__name__
    }
    return defined | set(_OTHER_MODEL_REFUSALS)


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


def test_model_policy_page_is_in_the_gateway_policies_nav() -> None:
    assert _PAGE.is_file()
    nav = (_CONTENT / "governance" / "gateway-policies" / "_meta.js").read_text(encoding="utf-8")
    assert "models: 'Models'" in nav


def test_model_policy_page_names_every_typed_model_refusal() -> None:
    text = _PAGE.read_text(encoding="utf-8")
    names = _model_refusals()
    assert "PIIDetected" in names and "TokenBudgetExceeded" in names
    for name in sorted(names):
        assert hasattr(donkey_kit, name), name
        assert f"`{name}`" in text, name


def test_crowdstrike_is_policy_source_never_live() -> None:
    section = _section(_PAGE.read_text(encoding="utf-8"), "## CrowdStrike AIDR Guardrails")
    assert "policy source" in section
    assert ">Policy-source</Badge>" in section
    assert not re.search(r'<Badge tone="live"', section)


def test_model_policy_page_is_linked_from_the_pages_it_summarises() -> None:
    for relative in (
        "errors.mdx",
        "budget.mdx",
        "reference/last-call.mdx",
        "custom-policies.mdx",
        "frameworks/index.mdx",
        "governance/gateway-policies/mcp.mdx",
        "governance/gateway-policies/a2a.mdx",
    ):
        assert _ROUTE in (_CONTENT / relative).read_text(encoding="utf-8"), relative

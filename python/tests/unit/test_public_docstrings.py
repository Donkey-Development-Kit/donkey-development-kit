"""The main public entry points document themselves in ``help()`` (#722).

Ruff's D101-D103 make every public symbol in ``src/`` carry *a* docstring; this
checks that the ones a developer meets first also carry a summary and a link to
the published docs, which is what ``help()`` and an IDE hover show.
"""

from __future__ import annotations

import pydoc

import pytest

from donkey_kit import Donkey
from donkey_kit.core.auth import AuthProvider
from donkey_kit.core.cache import TTLCache
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import PIIDetected, PromptInjectionBlocked, TokenBudgetExceeded
from donkey_kit.integrations.adk import ADKAdapter
from donkey_kit.integrations.agent_framework import AgentFrameworkAdapter
from donkey_kit.integrations.anthropic import AnthropicAdapter
from donkey_kit.integrations.crewai import CrewAIAdapter
from donkey_kit.integrations.langgraph import LangGraphAdapter
from donkey_kit.integrations.llamaindex import LlamaIndexAdapter
from donkey_kit.integrations.openai_agents import OpenAIAgentsAdapter
from donkey_kit.integrations.strands import StrandsAdapter
from donkey_kit.registry.exchange import ExchangeRegistry
from donkey_kit.tools.session import ToolSet

DOCS = "https://docs.donkey-kit.dev/"

ENTRY_POINTS: list[type] = [
    Donkey,
    DonkeyConfig,
    AuthProvider,
    TTLCache,
    ExchangeRegistry,
    ToolSet,
    TokenBudgetExceeded,
    PromptInjectionBlocked,
    PIIDetected,
    LangGraphAdapter,
    ADKAdapter,
    StrandsAdapter,
    AgentFrameworkAdapter,
    OpenAIAgentsAdapter,
    AnthropicAdapter,
    CrewAIAdapter,
    LlamaIndexAdapter,
]


@pytest.mark.parametrize("obj", ENTRY_POINTS, ids=lambda o: o.__name__)
def test_entry_point_docstring_has_summary_and_docs_link(obj: type) -> None:
    doc = obj.__doc__
    assert doc, f"{obj.__name__} has no docstring"
    summary = doc.strip().splitlines()[0]
    assert len(summary) > 10, f"{obj.__name__} summary line is too thin: {summary!r}"
    assert DOCS in doc, f"{obj.__name__} docstring has no link to {DOCS}"


@pytest.mark.parametrize("obj", [Donkey, PIIDetected], ids=lambda o: o.__name__)
def test_help_shows_summary_and_docs_link(obj: type) -> None:
    rendered = pydoc.render_doc(obj, renderer=pydoc.plaintext)
    assert obj.__doc__ is not None
    assert obj.__doc__.strip().splitlines()[0] in rendered
    assert DOCS in rendered


@pytest.mark.parametrize(
    "member", ["config", "llm", "registry", "tools", "aclose"], ids=lambda m: m
)
def test_donkey_core_members_are_documented(member: str) -> None:
    assert getattr(Donkey, member).__doc__

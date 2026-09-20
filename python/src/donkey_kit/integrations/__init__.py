"""integrations/ — one optional extra per framework (the layered architecture).

Each adapter returns NATIVE framework objects (BG §1.8). Modules are imported
lazily by :class:`donkey_kit.donkey.Donkey` so an uninstalled framework never
breaks ``import donkey_kit``.

The registry below maps the attribute name used on ``Donkey`` to the adapter's
module + class + pip extra, so ``Donkey.__getattr__`` can raise a curated
ImportError with the exact install command (BG §1.8).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterSpec:
    attr: str
    module: str
    cls: str
    extra: str
    #: Whether this adapter is held to the conformance suite in CI (`BG §1.8`).
    #: The roster is deliberately "one deep, seven shallow": only LangGraph is
    #: conformance-tested; the other seven are supported at ``connection_kwargs()``
    #: only. A second deep adapter is promoted from demand evidence, one at a time
    #: (#223/#244) — never guessed up front. Replaces the retired Tier 1/Tier 2
    #: split (#197).
    conformance_tested: bool
    #: A representative top-level module of the framework, probed with
    #: ``importlib.util.find_spec`` so ``Donkey.__getattr__`` can raise the
    #: curated ImportError at ACCESS time (BG §1.8). The adapters import their
    #: framework lazily inside methods, so importing the adapter module alone
    #: never fails — this probe is what makes access-time detection work.
    probe: str


# One deep, seven shallow (`BG §1.8`, #197): only LangGraph is conformance-tested;
# the other seven are supported at ``connection_kwargs()`` only. All eight keep
# their adapter, extra, and module-level factory — this is a support-tier flag,
# not a removal.
ADAPTERS: dict[str, AdapterSpec] = {
    "langgraph": AdapterSpec(
        "langgraph", ".langgraph", "LangGraphAdapter", "langgraph",
        conformance_tested=True, probe="langchain_openai",
    ),
    "adk": AdapterSpec(
        "adk", ".adk", "ADKAdapter", "adk",
        conformance_tested=False, probe="google.adk",
    ),
    "strands": AdapterSpec(
        "strands", ".strands", "StrandsAdapter", "strands",
        conformance_tested=False, probe="strands",
    ),
    "agent_framework": AdapterSpec(
        "agent_framework", ".agent_framework", "AgentFrameworkAdapter", "agent_framework",
        conformance_tested=False, probe="agent_framework",
    ),
    "openai_agents": AdapterSpec(
        "openai_agents", ".openai_agents", "OpenAIAgentsAdapter", "openai-agents",
        conformance_tested=False, probe="agents",
    ),
    "anthropic": AdapterSpec(
        "anthropic", ".anthropic", "AnthropicAdapter", "anthropic",
        conformance_tested=False, probe="anthropic",
    ),
    "crewai": AdapterSpec(
        "crewai", ".crewai", "CrewAIAdapter", "crewai",
        conformance_tested=False, probe="crewai",
    ),
    "llamaindex": AdapterSpec(
        "llamaindex", ".llamaindex", "LlamaIndexAdapter", "llamaindex",
        conformance_tested=False, probe="llama_index.llms.openai_like",
    ),
}

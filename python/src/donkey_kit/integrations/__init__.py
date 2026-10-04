"""integrations/ — one optional extra per framework (the layered architecture).

Each adapter returns NATIVE framework objects (BG §1.8). Modules are imported
lazily by :class:`donkey_kit.donkey.Donkey` so an uninstalled framework never
breaks ``import donkey_kit``.

The registry below maps the attribute name used on ``Donkey`` to the adapter's
module + class + pip extra, so ``Donkey.__getattr__`` can raise a curated
ImportError with the exact install command (BG §1.8).
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass

from ..core.refusals import Translator, TypedRefusals

__all__ = [
    "ADAPTERS",
    "AdapterSpec",
    "missing_framework_error",
    "refusal_translators",
    "typed_refusals",
]


@dataclass(frozen=True)
class AdapterSpec:
    """How ``Donkey`` finds one framework adapter: its attribute, module, class and pip extra.

    ``Donkey.__getattr__`` probes ``probe`` before importing ``module``, so a
    missing extra raises an ``ImportError`` carrying the install command (BG §1.8).
    """

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
    #: The modules the adapter's factories need, probed with
    #: ``importlib.util.find_spec`` so ``Donkey.__getattr__`` can raise the
    #: curated ImportError at ACCESS time (BG §1.8). The adapters import their
    #: framework lazily inside methods, so importing the adapter module alone
    #: never fails — this probe is what makes access-time detection work. It
    #: lists a required dependency the framework does not always install (Strands
    #: without ``openai``), so a half-installed framework fails here too (#741).
    probe: tuple[str, ...]
    #: The name of a module-level function in ``module`` that sees through the
    #: framework's own exception wrappers for the typed-refusal bridge (#724,
    #: ADR 0002), or ``None`` when the framework raises the HTTP SDK's errors
    #: as they are. The function has the :data:`~donkey_kit.core.refusals.Translator`
    #: signature. It holds no classification logic of its own: it unwraps and
    #: hands back to :func:`~donkey_kit.core.refusals.translate`.
    refusal_translator: str | None = None


def refusal_translators() -> tuple[Translator, ...]:
    """The per-adapter refusal translators of every framework already imported.

    A framework the process has not imported cannot have raised the exception
    being translated, so its adapter module is never imported here: the bridge
    adds no import cost (#724).
    """
    found: list[Translator] = []
    for spec in ADAPTERS.values():
        if spec.refusal_translator is None or spec.probe[0] not in sys.modules:
            continue
        module = importlib.import_module(spec.module, __name__)
        found.append(getattr(module, spec.refusal_translator))
    return tuple(found)


def typed_refusals() -> TypedRefusals:
    """Re-raise a governance refusal from the block as its typed ``DonkeyError``.

    The typed-refusal bridge (BG §1.2, #724, ADR 0002) as a standalone sync or
    async context manager, or a decorator. Any framework's or HTTP SDK's error
    that stands for a gateway refusal or a transport failure is re-raised as
    :class:`~donkey_kit.PIIDetected`, :class:`~donkey_kit.GatewayUnavailable`,
    :class:`~donkey_kit.ModelSubstituted` and the rest; anything else propagates
    unchanged. ``donkey.run()`` and ``@donkey.governed`` already apply it::

        with typed_refusals():
            graph.invoke({"messages": [...]})      # raises PIIDetected

        @typed_refusals()
        async def answer(question: str) -> str: ...

    Docs: https://docs.donkey-kit.dev/errors#typed-refusals-at-the-framework-boundary
    """
    return TypedRefusals(refusal_translators)


def missing_framework_error(extra: str, missing: str | None = None) -> ImportError:
    """The curated ImportError for an integration whose framework, or one of its
    required dependencies, is not installed (BG §1.8). Every form raises this one:
    ``Donkey.<framework>`` at access time, and each factory when its lazy import
    fails (#741). ``missing`` names the module that could not be found."""
    name = next((s.attr for s in ADAPTERS.values() if s.extra == extra), extra)
    detail = f" (no module named {missing!r})" if missing else ""
    return ImportError(
        f"The {name!r} integration is not installed{detail}. Install it with:\n"
        f'    pip install "donkey-kit[{extra}]"'
    )


# One deep, seven shallow (`BG §1.8`, #197): only LangGraph is conformance-tested;
# the other seven are supported at ``connection_kwargs()`` only. All eight keep
# their adapter, extra, and module-level factory — this is a support-tier flag,
# not a removal.
ADAPTERS: dict[str, AdapterSpec] = {
    "langgraph": AdapterSpec(
        "langgraph", ".langgraph", "LangGraphAdapter", "langgraph",
        conformance_tested=True, probe=("langchain_openai",),
    ),
    "adk": AdapterSpec(
        "adk", ".adk", "ADKAdapter", "adk",
        conformance_tested=False, probe=("google.adk", "litellm"),
    ),
    "strands": AdapterSpec(
        "strands", ".strands", "StrandsAdapter", "strands",
        conformance_tested=False, probe=("strands", "openai"),
        refusal_translator="refusal_translator",
    ),
    "agent_framework": AdapterSpec(
        "agent_framework", ".agent_framework", "AgentFrameworkAdapter", "agent_framework",
        conformance_tested=False, probe=("agent_framework",),
        refusal_translator="refusal_translator",
    ),
    "openai_agents": AdapterSpec(
        "openai_agents", ".openai_agents", "OpenAIAgentsAdapter", "openai-agents",
        conformance_tested=False, probe=("agents",),
    ),
    "anthropic": AdapterSpec(
        "anthropic", ".anthropic", "AnthropicAdapter", "anthropic",
        conformance_tested=False, probe=("anthropic",),
    ),
    "crewai": AdapterSpec(
        "crewai", ".crewai", "CrewAIAdapter", "crewai",
        conformance_tested=False, probe=("crewai",),
    ),
    "llamaindex": AdapterSpec(
        "llamaindex", ".llamaindex", "LlamaIndexAdapter", "llamaindex",
        conformance_tested=False, probe=("llama_index.llms.openai_like",),
    ),
}

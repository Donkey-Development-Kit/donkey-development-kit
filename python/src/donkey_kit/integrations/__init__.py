"""integrations/ — one optional extra per framework (the layered architecture).

Each adapter returns NATIVE framework objects (BG §1.8). Modules are imported
lazily by :class:`donkey_kit.donkey.Donkey` so an uninstalled framework never
breaks ``import donkey_kit``.

The :data:`ADAPTERS` roster below is the one place the adapter set is declared
(#726, ADR 0004). It maps the attribute name
used on ``Donkey`` to the adapter's module, class and pip extra, so
``Donkey.__getattr__`` can raise a curated ImportError with the exact install
command (BG §1.8). Everything else that lists the adapters (the pyproject
extras, the import-linter independence contract, the mypy overrides, the
``Donkey`` annotations, the conformance exemptions, the nightly matrix and
``scripts/verify_frameworks.py``) is checked against it by
``tests/unit/test_adapter_roster.py``.

Every adapter meets :class:`AdapterProtocol` and declares a frozen
:class:`AdapterCapabilities` per factory.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from ..core.refusals import Translator, TypedRefusals

__all__ = [
    "ADAPTERS",
    "AdapterCapabilities",
    "AdapterProtocol",
    "AdapterSpec",
    "AdapterTransport",
    "missing_framework_error",
    "refusal_translators",
    "typed_refusals",
]

#: Who sends an adapter's requests. ``"shared"``: the SDK's shared governed
#: client, handed to the framework directly, as a view, inside a pre-built
#: OpenAI client, or through the ``httpx2`` bridge. ``"framework"``: the
#: framework builds its own HTTP clients and the SDK supplies only the
#: connection values (CrewAI's native OpenAI provider).
AdapterTransport = Literal["shared", "framework"]


@dataclass(frozen=True)
class AdapterCapabilities:
    """What one adapter factory's native object gets from the SDK (#726).

    Declared per factory, on the adapter class, and never changed at runtime:
    ADK's ``model()`` and ``gemini()`` each have their own. Read it with
    ``donkey.<framework>.capabilities("<factory>")``; with no argument, the
    default factory's, which is the one ``connection_kwargs()`` configures.

    Every field states a fact about the SDK's own wiring, never a claim about
    the framework (§0.3). The conformance exemptions in ``KNOWN_LIMITATIONS``
    are checked against these values.
    """

    #: Who sends the requests (:data:`AdapterTransport`). In a token auth mode
    #: (jwt or bearer) a ``"framework"`` adapter is refused with ``ConfigError``:
    #: only the shared client adds the rotating token (#828, #836).
    transport: AdapterTransport
    #: Whether the native object's blocking calls go through the SDK's blocking
    #: client (LangGraph ``invoke()``, LlamaIndex ``complete()``). In a token
    #: auth mode that client refuses each send, since the token is async-only.
    sync: bool
    #: False when the governed connection turns the framework's streaming off
    #: (Strands sets ``stream=False``, #830); True when it is left as the
    #: framework has it.
    streaming: bool
    #: Whether a gateway refusal from this object reaches the caller typed
    #: through :func:`typed_refusals` (BG §1.2, #724). False where the framework
    #: raises its own errors for a call the SDK did not send or observe.
    typed_refusals: bool
    #: Whether a call through this object populates ``donkey.last_call`` (#362).
    #: Conservative: ADK ``model()``, LlamaIndex and Agent Framework now send
    #: through the shared client but still report False until their
    #: conformance exemptions are retired and the claim is tested (#740).
    observes_last_call: bool


@runtime_checkable
class AdapterProtocol(Protocol):
    """The contract every framework adapter meets (#726, BG §1.8).

    ``connection_kwargs()`` is the governed kwargs for the framework's own
    constructor, the whole supported surface of a ``connection_kwargs()``-only
    framework. ``capabilities()`` is the frozen :class:`AdapterCapabilities` of
    one factory. Code that handles any adapter can be typed against this
    instead of a concrete class.
    """

    def connection_kwargs(self) -> Mapping[str, Any]:
        """The governed kwargs for the framework's own client constructor."""
        ...

    def capabilities(self, factory: str | None = None) -> AdapterCapabilities:
        """The capabilities of ``factory``, by default the default factory's."""
        ...


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

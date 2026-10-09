"""Strands Agents adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

``client_args`` is forwarded to the underlying OpenAI client, so header AND
transport injection are both available (full injection). Strands builds and
closes a fresh ``AsyncOpenAI(**client_args)`` for every request, so the
``http_client`` it gets never closes: the shared client's non-owning view on
``openai<3``, a reusable bridge onto it (core ``httpx2`` bridge) on ``openai>=3``.
Closing it leaves the client usable for the next call and the shared client open
for every other surface (#733, #728).

STREAMING (#830): the governed connection sets ``stream=False``. On an
OpenAI-format proxy routing to Gemini the gateway answers a streamed request with
one whole ``chat.completion`` instead of chunk deltas, and Strands, which streams
by default, fails on every turn. Pass ``stream=True`` to stream on a route that
sends deltas.

Retries (#734, #951): ``client_args`` sets ``max_retries=0``, so the transport
alone retries. A Strands ``Agent`` adds its own throttle retry on top (6 attempts
by default): its ``ModelRetryStrategy`` retries every ``ModelThrottledException``,
which ``OpenAIModel.stream`` raises for any 429. A 429 here is a budget or
request-rate-limit refusal, terminal by contract (BG §1.2), so ``model()``
builds an ``OpenAIModel`` subclass whose ``stream`` raises the typed
``TokenBudgetExceeded`` or ``RequestRateLimitExceeded`` (#974) in its place.
The strategy does not retry it and the event loop re-raises it unwrapped, so a
default ``Agent`` sends a rate-limit refusal once (docs/verified-apis.md §8). A
throttle the SDK's transport did not send passes through, and Strands still
retries it. An ``OpenAIModel`` you build yourself from ``connection_kwargs()``
gets none of this: build its ``Agent`` with ``retry_strategy=None``.

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

import functools
from collections.abc import AsyncGenerator, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from ..core.errors import RequestRateLimitExceeded, TokenBudgetExceeded
from ..core.masking import masked
from ..core.refusals import TypedRefusals, translate
from . import AdapterCapabilities
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from strands.models.openai import OpenAIModel

    from ..core.errors import DonkeyError

__all__ = ["StrandsAdapter", "model", "refusal_translator"]


class StrandsAdapter(Adapter):
    """Governed Strands Agents objects, reached as ``donkey.strands``.

    Each factory returns the framework's own native object, pointed at the governed
    LLM proxy with the SDK's headers and transport: ``model(model)`` builds an
    ``OpenAIModel``. ``connection_kwargs()`` returns the same settings for building
    it yourself.

    Supported at ``connection_kwargs()`` only (`BG §1.8`): that accessor is the
    supported surface, and the factories are conveniences over it.

    Raises:
        ImportError: ``donkey.strands`` was read without the ``strands`` extra
            installed; the message carries the install command.
        ConfigError: The LLM-proxy settings are missing or incomplete.

    Docs: https://docs.donkey-kit.dev/frameworks/strands
    """

    # Async-only; the governed connection sets stream=False (#830, #726).
    factories = MappingProxyType(
        {
            "model": AdapterCapabilities(
                transport="shared",
                sync=False,
                streaming=False,
                typed_refusals=True,
                observes_last_call=True,
            ),
        }
    )

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``OpenAIModel(model_id=…, **kwargs)`` you build
        yourself. Strands forwards ``client_args`` to the underlying OpenAI
        client, so header AND transport injection are both available. ``stream``
        is ``False`` (see the module docstring, #830)."""
        conn = self._connection()
        return masked(
            {
                "client_args": {
                    **conn,  # base_url, api_key, default_headers
                    "http_client": self._openai_kwarg_http_client(),
                    "max_retries": 0,  # we retry in transport (BG §1.1)
                },
                "stream": False,
            }
        )

    def model(self, model: str, **kw: Any) -> OpenAIModel:
        """Return a native ``OpenAIModel`` at the proxy. A ``base_url`` in a
        ``client_args`` override must pass the https check. Pass ``stream=True``
        to stream (#830).

        The object is an ``OpenAIModel`` subclass that raises a rate-limit 429
        as the typed ``TokenBudgetExceeded`` or ``RequestRateLimitExceeded``, so
        a Strands ``Agent`` does not retry it (#951, #974, see the module
        docstring)."""
        client_args = kw.get("client_args")
        if isinstance(client_args, Mapping):
            self._allow_endpoints(client_args, "base_url")
        with self._native_import():
            from strands.models.openai import (
                OpenAIModel,  # VERIFY name/path: docs/verified-apis.md §8
            )

        governed = _governed_model_class(OpenAIModel)
        return governed(model_id=model, **{**self.connection_kwargs(), **kw})


@functools.cache
def _governed_model_class(base: Any) -> type[OpenAIModel]:
    """The subclass of Strands' ``OpenAIModel`` (``base``) that
    :meth:`StrandsAdapter.model` builds, defined on first use so ``strands`` is
    imported lazily (§1.1).

    Only ``stream`` is overridden, through the public ``Model.stream`` interface.
    Strands' event loop calls it for every model request, and its default
    ``ModelRetryStrategy.is_retryable`` retries only ``ModelThrottledException``
    (docs/verified-apis.md §8). A rate-limit 429 leaves as the typed refusal,
    and any other error, a throttle included, leaves unchanged."""

    class GovernedOpenAIModel(base):
        """Strands' ``OpenAIModel``, with a rate-limit 429 raised as the typed
        refusal instead of a retryable throttle (#951, #974)."""

        async def stream(self, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
            from strands.types.exceptions import ModelThrottledException

            try:
                async for event in super().stream(*args, **kwargs):
                    yield event
                return
            except ModelThrottledException as exc:
                typed = _rate_limit_refusal(exc)
                if typed is None:
                    raise
            # Raised outside the handler, as the typed-refusal bridge does: no
            # implicit context, and the throttle stays on ``framework_error``.
            raise typed from typed.__cause__

    return GovernedOpenAIModel


def _rate_limit_refusal(
    exc: BaseException,
) -> TokenBudgetExceeded | RequestRateLimitExceeded | None:
    """The typed rate-limit refusal behind a Strands throttle, or ``None`` when
    ``exc`` is not a governed token-budget or request-rate-limit refusal."""
    typed = TypedRefusals(lambda: (refusal_translator,)).resolve(exc)
    if isinstance(typed, (TokenBudgetExceeded, RequestRateLimitExceeded)):
        return typed
    return None


def model(model: str, **kw: Any) -> OpenAIModel:
    """Module-level convenience: a native ``OpenAIModel`` at the proxy using a
    cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().strands.model(model, **kw)``."""
    return default_adapter(StrandsAdapter).model(model, **kw)


def refusal_translator(exc: BaseException) -> DonkeyError | None:
    """See through Strands' exception wrappers for the typed-refusal bridge (#724).

    Strands re-raises the HTTP SDK's error inside its own types, which carry no
    ``request`` or ``response``: ``ModelThrottledException`` and
    ``ContextWindowOverflowException`` from its OpenAI model, and
    ``EventLoopException`` from the event loop, each ``from`` the original. This
    hands the wrapped error back to :func:`~donkey_kit.core.refusals.translate`;
    the classification itself stays in core. Registered as the adapter's
    ``AdapterSpec.refusal_translator`` and only consulted once ``strands`` is
    imported.
    """
    from strands.types import exceptions

    wrappers = (
        exceptions.ModelThrottledException,
        exceptions.ContextWindowOverflowException,
        exceptions.EventLoopException,
    )
    if isinstance(exc, wrappers) and exc.__cause__ is not None:
        return translate(exc.__cause__, (refusal_translator,))
    return None

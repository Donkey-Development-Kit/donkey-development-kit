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

Retries (#734): ``client_args`` sets ``max_retries=0``, so the transport alone
retries. A Strands ``Agent`` adds its own throttle retry on top (6 attempts by
default) and takes every 429 for a throttle, while a 429 here is a budget
refusal (BG §1.2). Build it with ``Agent(retry_strategy=None)``.

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..core.masking import masked
from ..core.refusals import translate
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

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``OpenAIModel(model_id=…, **kwargs)`` you build
        yourself. Strands forwards ``client_args`` to the underlying OpenAI
        client, so header AND transport injection are both available. ``stream``
        is ``False`` (see the module docstring, #830)."""
        conn = self._openai_connection()
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
        to stream (#830)."""
        client_args = kw.get("client_args")
        if isinstance(client_args, Mapping):
            self._allow_endpoints(client_args, "base_url")
        with self._native_import():
            from strands.models.openai import (
                OpenAIModel,  # VERIFY name/path: docs/verified-apis.md §8
            )

        return OpenAIModel(model_id=model, **{**self.connection_kwargs(), **kw})


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

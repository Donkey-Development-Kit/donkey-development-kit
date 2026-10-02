"""Masked rendering for mappings that carry credentials.

``connection_kwargs()`` and :func:`~donkey_kit.core.transport.proxy_auth_headers`
return a :class:`MaskedDict`: a real ``dict`` — ``**`` unpacking, lookups, ``==``,
``copy``, ``json.dumps`` and ``isinstance(x, dict)`` all behave as before, and the
real values reach the framework — whose ``repr()``/``str()`` show ``'***'`` in
place of every value under a name in :data:`SENSITIVE_NAMES`. Printing, logging,
a pytest diff or a traceback with locals therefore never shows the secret.

``dict(x)`` is the explicit unmasked view; copies made with ``x.copy()``,
``copy.copy``/``copy.deepcopy`` or ``x | other`` stay masked.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from . import _verify

V = TypeVar("V")

#: What a masked value renders as.
MASK = "***"

#: Lower-cased key and header names whose values are credentials. The single
#: list every masked mapping consults; matched case-insensitively.
SENSITIVE_NAMES: frozenset[str] = frozenset(
    name.lower()
    for name in (
        "api_key",  # the OpenAI-compatible SDK slot (llm_proxy_key)
        "apikey",
        "x-api-key",
        "api-key",
        "x-goog-api-key",  # google-genai's API-key header
        "cookie",
        _verify.LLM_PROXY_CLIENT_SECRET_HEADER,  # client-id enforcement pair
        _verify.LLM_PROXY_WALLET_JWT_HEADER,  # Authorization: Bearer <JWT>
        "proxy-authorization",
    )
)


def is_sensitive(name: object) -> bool:
    """Whether ``name`` is a key or header name whose value is a credential."""
    return isinstance(name, str) and name.lower() in SENSITIVE_NAMES


class MaskedDict(dict[str, V]):
    """A ``dict`` whose ``repr()``/``str()`` mask every value under a
    :data:`SENSITIVE_NAMES` key. Everything else is plain ``dict`` behaviour."""

    __slots__ = ()

    def __repr__(self) -> str:
        items = ", ".join(
            f"{key!r}: {MASK!r}" if is_sensitive(key) else f"{key!r}: {value!r}"
            for key, value in self.items()
        )
        return "{" + items + "}"

    __str__ = __repr__

    def copy(self) -> MaskedDict[V]:
        """Return a shallow copy that still masks its secrets."""
        return MaskedDict(self)

    __copy__ = copy

    def __or__(self, other: Mapping[str, V]) -> MaskedDict[V]:  # type: ignore[override]
        merged = MaskedDict(self)
        merged.update(other)
        return merged

    def __ror__(self, other: Mapping[str, V]) -> MaskedDict[V]:  # type: ignore[override]
        merged: MaskedDict[V] = MaskedDict(other)
        merged.update(self)
        return merged


def masked(mapping: Mapping[str, V]) -> MaskedDict[V]:
    """A :class:`MaskedDict` of ``mapping``, with every nested ``dict`` value
    (e.g. ``default_headers`` inside ``client_args``) masked too."""
    return MaskedDict(
        {
            key: cast(V, masked(cast(Mapping[str, Any], value)))
            if isinstance(value, dict)
            else value
            for key, value in mapping.items()
        }
    )

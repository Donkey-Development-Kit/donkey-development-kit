"""The HTTP response shape the error taxonomy reads, on either HTTP stack (#933).

openai>=3 and anthropic>=1 type ``APIStatusError.response`` as
``httpx2.Response``, a separate class from ``httpx.Response``. Both satisfy these
structural Protocols, so ``classify(exc.response)`` type-checks on either stack
without ``core/`` importing ``httpx2`` (§1.1). Re-exported from
:mod:`donkey_kit.core.errors`, which owns the public name.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

__all__ = ["RequestLike", "ResponseLike"]


class RequestLike(Protocol):
    """The request half of :class:`ResponseLike`: what ``classify()`` reads back
    to recover the ids the client sent (BG §1.1, #195)."""

    @property
    def headers(self) -> Mapping[str, str]:
        """The headers sent, carrying the correlation and call ids."""

    @property
    def extensions(self) -> Mapping[str, Any]:
        """The transport's stamp of the header names it used (#363)."""


class ResponseLike(Protocol):
    """The HTTP response ``classify()`` reads and ``DonkeyError.response``
    carries. ``request`` may raise ``RuntimeError`` when none was set, as both
    stacks do."""

    @property
    def status_code(self) -> int:
        """The HTTP status."""

    @property
    def headers(self) -> Mapping[str, str]:
        """The response headers, where most rejection discriminators live."""

    @property
    def request(self) -> RequestLike:
        """The request that produced this response."""

    @property
    def content(self) -> bytes:
        """The raw body."""

    @property
    def text(self) -> str:
        """The decoded body."""

    def json(self, **kwargs: Any) -> Any:  # noqa: ANN401 - mirrors httpx's own return
        """The parsed JSON body."""

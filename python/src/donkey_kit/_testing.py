"""Internal seams the dev-only siblings reach through (#719). Not public API.

The simulator's ``simulate()`` (BG §1.5) and the conformance harness (BG §1.5)
need to swap a fixture transport onto a ``Donkey``'s live HTTP clients and see
which adapters an agent resolved. Those are private details of
:class:`~donkey_kit.core.transport.DonkeyAsyncClient` /
:class:`~donkey_kit.core.transport.DonkeyClient` and
:class:`~donkey_kit.donkey.Donkey`. Rather than have each sibling reach into
another package's underscore members, every such access lives here, in one
private module, under one justified ruff ``SLF001`` exemption
(``pyproject.toml``). Nothing in the production layers imports this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .core.transport import DonkeyAsyncClient, DonkeyClient
    from .donkey import Donkey
    from .integrations._base import Adapter

__all__ = [
    "SwappableClient",
    "http_clients",
    "resolved_adapters",
    "swap_transport",
    "transport_of",
]


class SwappableClient(Protocol):
    """The transport-swap seam both Donkey HTTP clients expose (BG §1.1, #728).
    Typed loosely on purpose: the sync and async clients carry different
    governed-transport types (``GovernedTransport`` / ``GovernedSyncTransport``)."""

    @property
    def governed_transport(self) -> Any: ...


def transport_of(client: SwappableClient) -> Any:
    """The transport ``client`` sends through right now: the inner transport its
    ``governed_transport`` delegates to."""
    return client.governed_transport.inner


def swap_transport(client: SwappableClient, transport: Any) -> None:
    """Make ``transport`` the one ``client`` sends through, via the public
    ``GovernedTransport.replace_inner()`` seam, which refuses while an env-proxy
    mount could bypass it (#801, #728)."""
    client.governed_transport.replace_inner(transport)


def http_clients(donkey: Donkey) -> tuple[DonkeyAsyncClient, DonkeyClient]:
    """The async and sync governed clients behind ``donkey``. The sync client is
    built on first use; building it opens no connection."""
    return donkey._http, donkey._sync_http_client()


def resolved_adapters(donkey: Donkey) -> Mapping[str, Adapter]:
    """The adapters resolved on ``donkey`` so far, by attribute name."""
    return donkey._adapter_cache

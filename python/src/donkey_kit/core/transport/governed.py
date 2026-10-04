"""The transports under a governed client (#801, #807, #728).

:class:`GovernedTransport` (and its blocking twin) is the one transport a
:class:`~donkey_kit.core.transport.DonkeyAsyncClient` sends through. It owns an
inner transport, and :meth:`GovernedTransport.replace_inner` swaps that inner
transport on a live client: the public seam ``simulate()`` (BG §1.5) and the
conformance harness use to put a fixture under the governed pipeline, in place
of writing httpx's private ``_transport``. Beneath it sit the per-loop pool and
the mount routers the constructors build.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterable, Sized
from types import TracebackType
from typing import Generic, Protocol, TypeVar

import httpx

__all__ = ["GovernedSyncTransport", "GovernedTransport"]

_T = TypeVar("_T")


class _InnerSlot(Generic[_T]):
    """The replaceable inner transport both governed transports share."""

    def __init__(self, inner: _T, mounts: Callable[[], Sized]) -> None:
        self._inner: _T = inner
        self._mounts = mounts

    @property
    def inner(self) -> _T:
        """The transport requests go to right now."""
        return self._inner

    def replace_inner(self, transport: _T) -> None:
        """Send every later request through ``transport``. httpx resolves the
        transport per send from the client's mounts and then its base transport;
        the constructor folds every mount (including ``HTTP(S)_PROXY`` /
        ``ALL_PROXY`` proxies from the environment) into the base transport, so
        the next request uses ``transport`` with no reconstruction and no route
        around it (#801).

        Raises:
            RuntimeError: The client has a mount, added after construction, that
                would route some requests past ``transport`` to a real connection.
        """
        if self._mounts():
            raise RuntimeError(
                "cannot swap the transport: this client has httpx mounts that would "
                "bypass it and open real connections. Pass proxies via mounts=/proxy= "
                "at construction so the client folds them into its transport."
            )
        self._inner = transport


class GovernedTransport(_InnerSlot[httpx.AsyncBaseTransport], httpx.AsyncBaseTransport):
    """The async client's own transport: it forwards to :attr:`inner`, which
    :meth:`replace_inner` swaps."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Send ``request`` through the current inner transport."""
        return await self._inner.handle_async_request(request)

    async def __aenter__(self) -> GovernedTransport:
        await self._inner.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        await self._inner.__aexit__(exc_type, exc_value, traceback)

    async def aclose(self) -> None:
        """Close the current inner transport."""
        await self._inner.aclose()


class GovernedSyncTransport(_InnerSlot[httpx.BaseTransport], httpx.BaseTransport):
    """Blocking twin of :class:`GovernedTransport`."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Send ``request`` through the current inner transport."""
        return self._inner.handle_request(request)

    def __enter__(self) -> GovernedSyncTransport:
        self._inner.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        self._inner.__exit__(exc_type, exc_value, traceback)

    def close(self) -> None:
        """Close the current inner transport."""
        self._inner.close()


class _LoopLocalTransport(httpx.AsyncBaseTransport):
    """One connection pool per event loop (#807).

    An httpx pool's keep-alive connections belong to the loop that opened them.
    A sync app that wraps each call in ``asyncio.run()`` (a Flask or Django
    view, a CLI, a per-test pytest-asyncio loop) gets a new loop per call, and
    reusing the old loop's pool fails with a raw ``RuntimeError: Event loop is
    closed``. This wrapper takes the place of each pool httpx builds for a
    :class:`DonkeyAsyncClient`, and sends each request through the pool of the
    running loop. The pool httpx built goes to the first loop that sends. Each
    later loop gets a fresh pool from ``build``, with the same settings, so
    loops in different threads never share connections. A closed loop's pool
    is dropped when the next new loop arrives, so one ``asyncio.run()`` per
    request does not pile up dead pools.

    It wraps the pool rather than overriding the client, so a transport swapped
    in on top (``simulate()``, the conformance probe) still delegates to the
    right pool when it passes a request through.
    """

    def __init__(
        self, first: httpx.AsyncBaseTransport, build: Callable[[], httpx.AsyncBaseTransport]
    ) -> None:
        self._first: httpx.AsyncBaseTransport | None = first
        self._build = build
        # A plain dict, not a WeakKeyDictionary: a pool's connections hold
        # their loop, so the loop key would never be collected anyway.
        self._pools: dict[asyncio.AbstractEventLoop, httpx.AsyncBaseTransport] = {}
        self._lock = threading.Lock()

    def _pool(self) -> httpx.AsyncBaseTransport:
        loop = asyncio.get_running_loop()
        pool = self._pools.get(loop)
        if pool is None:
            with self._lock:
                pool = self._pools.get(loop)
                if pool is None:
                    for closed in [old for old in self._pools if old.is_closed()]:
                        del self._pools[closed]
                    pool, self._first = self._first or self._build(), None
                    self._pools[loop] = pool
        return pool

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._pool().handle_async_request(request)

    async def aclose(self) -> None:
        # Close only the running loop's pool. A pool on another loop is dropped
        # unclosed: that loop is closed (``asyncio.run`` returned) or runs in
        # another thread, and closing its sockets from here would fail.
        loop = asyncio.get_running_loop()
        with self._lock:
            pool = self._pools.get(loop) or self._first
            self._pools.clear()
            self._first = None
        if pool is not None:
            await pool.aclose()


class _UrlPattern(Protocol):
    """The one method of httpx's (private) mount-key ``URLPattern`` we use."""

    def matches(self, other: httpx.URL) -> bool: ...


class _AsyncMountRouter(httpx.AsyncBaseTransport):
    """The client's base transport and its proxy mounts, folded into one
    transport (#801).

    httpx turns ``HTTP(S)_PROXY`` / ``ALL_PROXY`` (``trust_env``), ``proxy=`` and
    ``mounts=`` into ``client._mounts``, which it checks *before*
    ``client._transport``. A swap that replaces only ``_transport`` is bypassed
    by them, so ``simulate()`` and the conformance probe went online behind a
    corporate proxy. Folding the mounts in here leaves ``_mounts`` empty, so
    ``_transport`` is the only route: a swap takes every request, and a
    fixture's pass-through to this router still honours the proxy settings.
    Routing mirrors httpx's ``_transport_for_url``: the first matching pattern
    wins, and a ``None`` mount (a ``NO_PROXY`` entry) means the default."""

    def __init__(
        self,
        default: httpx.AsyncBaseTransport,
        mounts: Iterable[tuple[_UrlPattern, httpx.AsyncBaseTransport | None]],
    ) -> None:
        self._default = default
        self._routes = list(mounts)

    def _children(self) -> list[httpx.AsyncBaseTransport]:
        return [self._default, *(t for _, t in self._routes if t is not None)]

    def _route(self, url: httpx.URL) -> httpx.AsyncBaseTransport:
        for pattern, transport in self._routes:
            if pattern.matches(url):
                return self._default if transport is None else transport
        return self._default

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._route(request.url).handle_async_request(request)

    async def __aenter__(self) -> _AsyncMountRouter:
        for transport in self._children():
            await transport.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        for transport in self._children():
            await transport.__aexit__(exc_type, exc_value, traceback)

    async def aclose(self) -> None:
        for transport in self._children():
            await transport.aclose()


class _SyncMountRouter(httpx.BaseTransport):
    """Blocking twin of :class:`_AsyncMountRouter`."""

    def __init__(
        self,
        default: httpx.BaseTransport,
        mounts: Iterable[tuple[_UrlPattern, httpx.BaseTransport | None]],
    ) -> None:
        self._default = default
        self._routes = list(mounts)

    def _children(self) -> list[httpx.BaseTransport]:
        return [self._default, *(t for _, t in self._routes if t is not None)]

    def _route(self, url: httpx.URL) -> httpx.BaseTransport:
        for pattern, transport in self._routes:
            if pattern.matches(url):
                return self._default if transport is None else transport
        return self._default

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._route(request.url).handle_request(request)

    def __enter__(self) -> _SyncMountRouter:
        for transport in self._children():
            transport.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        for transport in self._children():
            transport.__exit__(exc_type, exc_value, traceback)

    def close(self) -> None:
        for transport in self._children():
            transport.close()

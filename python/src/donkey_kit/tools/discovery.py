"""``donkey.tools.discover()``: Exchange MCP assets to a bindable ``ToolSet`` (BG §2.7, #221).

Composes three live registry reads, nothing new on the wire: ``search()`` for
the MCP assets (docs/verified-apis.md §7), ``explain()`` for each one's
governed state, and ``resolve_mcp()`` for its gateway endpoint (§6). None of
them opens an MCP connection; the ``ToolSet`` connects when it binds.

Nothing here branches on where a server runs. A Mule-hosted server and a
framework-built one are both an Exchange ``mcp`` asset with an API Manager
instance on a gateway, and are discovered and bound the same way.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from ..core import _verify, telemetry
from ..core.approvals import NativePause
from ..core.cache import TTLCache
from ..core.config import DonkeyConfig
from ..core.errors import AuthError, RegistryError
from ..core.identity import user_token_header
from ..registry.criteria import Check, GovernanceCriteria, GovernanceReport
from ..registry.exchange import ExchangeRegistry
from ..registry.models import AssetRef, McpServerHandle
from .auth import McpAuth, check_gateway_endpoint
from .session import ToolSet

__all__ = ["Discovery"]

_log = logging.getLogger(__name__)

#: How many MCP assets one discover() call reads at most; a catalog that reaches
#: it is logged, never cut silently.
DISCOVER_LIMIT = 200
#: Concurrent explain()/resolve_mcp() reads, so a large catalog does not burst
#: the control plane.
_CONCURRENCY = 8

#: One span per discover() or warm() call (#1323). Its attributes say whether the
#: registry was read, so a cache hit and a read are told apart from the trace.
SPAN_DISCOVER = "donkey.tools.discover"
SPAN_WARM = "donkey.tools.warm"
#: ``hit`` when the set came from the cache, ``miss`` when the registry was read.
DONKEY_DISCOVER_CACHE = "donkey.discover.cache"
#: Servers in the returned set, and MCP assets left out as ungoverned.
DONKEY_DISCOVER_SERVERS = "donkey.discover.servers"
DONKEY_DISCOVER_LEFT_OUT = "donkey.discover.left_out"


@dataclasses.dataclass(frozen=True)
class _Found:
    servers: tuple[McpServerHandle, ...]
    at: datetime
    started: float
    left_out: int = 0


class Discovery:
    """The state behind ``donkey.tools``: the discovery cache and what every
    discovered ``ToolSet`` binds with.

    Args:
        registry: The Exchange registry client.
        cfg: The resolved configuration (``registry_cache_ttl_s`` and the
            ``tool_warn_*`` thresholds).
        auth: The default gateway credential for bindings, built from ``cfg``
            by :func:`~donkey_kit.tools.auth.gateway_auth`; the endpoint check
            weighs it by the source of the fields it was built from.
        natives: The framework approval pauses (#1153).
    """

    def __init__(
        self,
        registry: ExchangeRegistry,
        cfg: DonkeyConfig,
        *,
        auth: McpAuth | None,
        natives: tuple[NativePause, ...] = (),
    ) -> None:
        self._registry = registry
        self._cfg = cfg
        self._auth = auth
        self._natives = natives
        self._cache: TTLCache[_Found] = TTLCache(ttl_s=cfg.registry_cache_ttl_s)
        self._said_unfiltered = False

    async def discover(
        self,
        *,
        domain: str | None,
        tags: Sequence[str] | None,
        governed: bool | GovernanceCriteria | None,
        auth: McpAuth | None,
    ) -> ToolSet:
        """See ``ToolsFacade.discover``."""
        criteria = governed if isinstance(governed, GovernanceCriteria) else GovernanceCriteria()
        if governed is None and not self._said_unfiltered:
            self._said_unfiltered = True
            _log.info(
                "donkey.tools.discover() is unfiltered (governed=None): it lists every MCP "
                "server, and binding refuses an ungoverned one. Leave governed at its "
                "default (True) to leave those out."
            )
        key = self._key(domain, tags, governed, criteria)
        with self._span(SPAN_DISCOVER) as record:
            found = self._cache.get(key)
            hit = found is not None
            if found is None:
                found = await self._fill(key, domain, tags, governed, criteria)
            record(found, hit)
        return ToolSet(
            list(found.servers),
            _natives=self._natives,
            _auth=auth if auth is not None else self._auth,
            _discovered=(found.at, found.started, self._cfg.registry_cache_ttl_s),
            _limits=(self._cfg.tool_warn_count, self._cfg.tool_warn_tokens),
            _user_header=user_token_header(self._cfg),
            _endpoint_check=self._endpoint_check(auth),
        )

    def _endpoint_check(self, auth: McpAuth | None) -> Callable[[bool, bool], None]:
        """The check a set runs before it lists or binds, given whether the
        gateway credential and the ``donkey.as_user()`` token go: the control
        plane the server URLs came from may refuse them (ADR 0019, #1479).
        ``auth`` is the ``discover(auth=...)`` credential, set in code; ``None``
        is the default one, built from the config."""
        origin: Literal["config", "code"] = "config" if auth is None else "code"

        def check(credential: bool, user: bool) -> None:
            check_gateway_endpoint(self._cfg, credential=origin if credential else None, user=user)

        return check

    async def warm(
        self,
        *,
        domain: str | None,
        tags: Sequence[str] | None,
        governed: bool | GovernanceCriteria | None,
    ) -> int:
        """See ``ToolsFacade.warm``."""
        criteria = governed if isinstance(governed, GovernanceCriteria) else GovernanceCriteria()
        key = self._key(domain, tags, governed, criteria)
        with self._span(SPAN_WARM) as record:
            # The registry caches its lookups too; warm() reads past both (#1419).
            self._registry.refresh()
            found = await self._fill(key, domain, tags, governed, criteria)
            record(found, False)
        return len(found.servers)

    @contextlib.contextmanager
    def _span(self, name: str) -> Iterator[Callable[[_Found, bool], None]]:
        """A span around one discovery, and the callback that sets its outcome.
        Inert when telemetry is off or OpenTelemetry is not installed. A failure
        sets ``error.type`` to the exception's class name only, never its message."""
        tracer = telemetry.tracer() if self._cfg.telemetry else None
        if tracer is None:
            yield _no_record
            return
        from opentelemetry.trace import Status, StatusCode

        with tracer.start_as_current_span(
            name, record_exception=False, set_status_on_exception=False
        ) as span:

            def record(found: _Found, hit: bool) -> None:
                span.set_attribute(DONKEY_DISCOVER_CACHE, "hit" if hit else "miss")
                span.set_attribute(DONKEY_DISCOVER_SERVERS, len(found.servers))
                span.set_attribute(DONKEY_DISCOVER_LEFT_OUT, found.left_out)

            try:
                yield record
            except Exception as exc:
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR))
                raise

    def _key(
        self,
        domain: str | None,
        tags: Sequence[str] | None,
        governed: bool | GovernanceCriteria | None,
        criteria: GovernanceCriteria,
    ) -> str:
        return "discover:" + json.dumps(
            [
                self._cfg.environment,
                domain,
                sorted(tags or ()),
                bool(governed),
                dataclasses.asdict(criteria),
            ],
            sort_keys=True,
        )

    async def _fill(
        self,
        key: str,
        domain: str | None,
        tags: Sequence[str] | None,
        governed: bool | GovernanceCriteria | None,
        criteria: GovernanceCriteria,
    ) -> _Found:
        found = await self._read(domain, tags, criteria, keep_ungoverned=not governed)
        self._cache.set(key, found)
        return found

    async def resolve(self, ref: AssetRef | str, *, auth: McpAuth | None) -> ToolSet:
        """A ``ToolSet`` of the one server ``ref``: its default-criteria verdict
        and its endpoint, as :meth:`discover` reads them for each asset, with no
        search and no cache. A server that does not resolve is in the set with
        a failed ``resolve_mcp`` check, so listing its tools refuses it."""
        handle = await self._handle(AssetRef.parse(ref), GovernanceCriteria())
        return ToolSet(
            [handle],
            _natives=self._natives,
            _auth=auth if auth is not None else self._auth,
            _limits=(self._cfg.tool_warn_count, self._cfg.tool_warn_tokens),
            _user_header=user_token_header(self._cfg),
            _endpoint_check=self._endpoint_check(auth),
        )

    async def live_tools(self, ref: AssetRef | str) -> tuple[dict[str, object], ...]:
        """The tools MCP server ``ref`` lists now: its ``tools/list`` through its
        gateway instance, after the governance check and with the credential a
        :meth:`resolve` binding uses (§9). What ``Publication.verify()`` compares
        the published ``mcp-metadata`` tools against (#991). Not cached."""
        (server,) = (await (await self.resolve(ref, auth=None)).describe()).servers
        return server.tool_descriptors or ()

    async def agent_card(self, ref: AssetRef | str) -> dict[str, Any]:
        """The A2A card agent ``ref`` serves through its gateway instance (#1354):
        ``GET <endpoint>/.well-known/agent-card.json``, with the gateway
        credential a ``ToolSet`` binding sends (docs/verified-apis.md §7).

        Like a binding, it reads the default-criteria verdict first and sends
        nothing to an agent that is not governed (ADR 0013), nor to a gateway
        URL read from a control plane the credential may not go to (ADR 0019).
        Not cached.

        Raises:
            RegistryError: The agent is not governed, does not resolve to one
                deployed A2A instance behind an ``https://`` gateway URL, or the
                card read failed.
            ConfigError: ``base_url`` was read from the working directory's
                config files and the credential was not
                (:func:`~donkey_kit.tools.auth.check_gateway_endpoint`).
            AuthError: The gateway refused the credential (``401``/``403``).
        """
        asset = AssetRef.parse(ref)
        report = await self._registry.explain(asset, criteria=GovernanceCriteria())
        if not report.governed:
            raise RegistryError(
                f"Refusing to read the live card of {asset.coordinates}: it is not governed "
                f"({'; '.join(report.reasons_failed()) or 'not governed'}). No gateway "
                "credentials were sent.",
                remediation=(
                    "Front the agent with the gateway so its policies apply. Run "
                    "donkey.registry.explain(ref) to see which governance check failed."
                ),
            )
        handle = await self._registry.resolve_agent(asset)
        url = handle.endpoint_url + ".well-known/agent-card.json"
        check_gateway_endpoint(
            self._cfg, credential="config" if self._auth is not None else None, user=False
        )
        headers = await self._auth.headers() if self._auth is not None else {}
        # Its own client, as an MCP binding's is: the shared ones send credentials
        # only to their configured origins. Redirects are not followed, so the
        # credential goes to the gateway and nowhere else.
        try:
            async with _card_client(self._cfg.timeout_s) as http:
                response = await http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            detail = f"{type(exc).__name__}: {exc}"
            raise RegistryError(
                f"Reading the live card of {asset.coordinates} failed ({detail})."
            ) from exc
        if response.status_code in (401, 403):
            raise AuthError(
                f"The gateway refused the credential when reading the card of "
                f"{asset.coordinates} (HTTP {response.status_code} {response.text[:200]})."
            )
        if not response.is_success:
            raise RegistryError(
                f"Reading the live card of {asset.coordinates} failed: HTTP "
                f"{response.status_code} {response.text[:200]}"
            )
        try:
            card = response.json()
        except ValueError as exc:
            raise RegistryError(f"The live card of {asset.coordinates} is not JSON.") from exc
        if not isinstance(card, dict):
            raise RegistryError(f"The live card of {asset.coordinates} is not a JSON object.")
        return card

    def refresh(self) -> None:
        """Drop every cached discovery and the registry's cached lookups, so the
        next ``discover()`` reads Exchange and API Manager again."""
        self._cache.invalidate()
        self._registry.refresh()

    async def _read(
        self,
        domain: str | None,
        tags: Sequence[str] | None,
        criteria: GovernanceCriteria,
        *,
        keep_ungoverned: bool,
    ) -> _Found:
        at, started = datetime.now(timezone.utc), time.monotonic()
        refs = await self._registry.search(
            asset_types=["mcp"],
            tags=list(tags) if tags else None,
            domain=domain,
            limit=DISCOVER_LIMIT,
        )
        if len(refs) == DISCOVER_LIMIT:
            _log.warning(
                "donkey.tools.discover() read the first %d MCP assets only; narrow it with "
                "tags= to see the rest.",
                DISCOVER_LIMIT,
            )
        gate = asyncio.Semaphore(_CONCURRENCY)

        async def one(ref: AssetRef) -> McpServerHandle:
            async with gate:
                return await self._handle(ref, criteria)

        handles = await asyncio.gather(*(one(ref) for ref in refs))
        kept: list[McpServerHandle] = []
        for handle in handles:
            report = handle.governance
            if keep_ungoverned or (report is not None and report.governed):
                kept.append(handle)
            else:
                _log.info(
                    "donkey.tools.discover() left out %s: %s",
                    handle.ref.coordinates,
                    "; ".join(report.reasons_failed() if report else ()) or "not governed",
                )
        if not kept:
            where = f"in environment {self._cfg.environment!r}"
            _log.info(
                "donkey.tools.discover() found no MCP server to bind %s: %s",
                where,
                f"{len(refs)} MCP asset(s) matched, none governed; run "
                "donkey.registry.explain(ref) to see why"
                if refs
                else f"no MCP asset in Exchange matched tags={list(tags or [])!r}",
            )
        return _Found(tuple(kept), at, started, left_out=len(handles) - len(kept))

    async def _handle(self, ref: AssetRef, criteria: GovernanceCriteria) -> McpServerHandle:
        """``ref``'s endpoint with its verdict. A server that does not resolve to
        exactly one deployed MCP instance is listed with an empty endpoint and a
        failed ``resolve_mcp`` check, so binding refuses it with that reason."""
        report = await self._registry.explain(ref, criteria=criteria)
        try:
            handle = await self._registry.resolve_mcp(ref)
        except RegistryError as exc:
            failed = Check("resolve_mcp", False, str(exc))
            report = GovernanceReport(governed=False, checks=[*report.checks, failed])
            return McpServerHandle(ref=ref, endpoint_url="", governance=report)
        return dataclasses.replace(handle, ref=ref, governance=report)


def _card_client(timeout_s: float) -> httpx.AsyncClient:
    """The client an agent card is read with; a test seam."""
    return httpx.AsyncClient(timeout=timeout_s)


def _no_record(_found: _Found, _hit: bool) -> None:
    """The outcome callback when no span is recorded."""


def blocked_lock() -> NotImplementedError:
    """``discover(locked=True)``: pinned resolution waits on the lockfile."""
    return _verify.blocked(
        "discover(locked=True): resolving only what donkey.lock pins needs the lockfile "
        "(BG §2.7, donkey.tools.lock())."
    )

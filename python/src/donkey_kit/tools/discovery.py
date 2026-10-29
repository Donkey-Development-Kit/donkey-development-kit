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
import dataclasses
import json
import logging
import time
from collections.abc import Sequence
from datetime import datetime, timezone

from ..core import _verify
from ..core.approvals import NativePause
from ..core.cache import TTLCache
from ..core.config import DonkeyConfig
from ..core.errors import RegistryError
from ..registry.criteria import Check, GovernanceCriteria, GovernanceReport
from ..registry.exchange import ExchangeRegistry
from ..registry.models import AssetRef, McpServerHandle
from .auth import McpAuth
from .session import ToolSet

__all__ = ["Discovery"]

_log = logging.getLogger(__name__)

#: How many MCP assets one discover() call reads at most; a catalog that reaches
#: it is logged, never cut silently.
DISCOVER_LIMIT = 200
#: Concurrent explain()/resolve_mcp() reads, so a large catalog does not burst
#: the control plane.
_CONCURRENCY = 8


@dataclasses.dataclass(frozen=True)
class _Found:
    servers: tuple[McpServerHandle, ...]
    at: datetime
    started: float


class Discovery:
    """The state behind ``donkey.tools``: the discovery cache and what every
    discovered ``ToolSet`` binds with.

    Args:
        registry: The Exchange registry client.
        cfg: The resolved configuration (``registry_cache_ttl_s`` and the
            ``tool_warn_*`` thresholds).
        auth: The default gateway credential for bindings.
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
                "server, and binding refuses an ungoverned one. Pass governed=True to leave "
                "those out."
            )
        key = self._key(domain, tags, governed, criteria)
        found = self._cache.get(key)
        if found is None:
            found = await self._fill(key, domain, tags, governed, criteria)
        return ToolSet(
            list(found.servers),
            _natives=self._natives,
            _auth=auth if auth is not None else self._auth,
            _discovered=(found.at, found.started, self._cfg.registry_cache_ttl_s),
            _limits=(self._cfg.tool_warn_count, self._cfg.tool_warn_tokens),
        )

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
        return len((await self._fill(key, domain, tags, governed, criteria)).servers)

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
        )

    def refresh(self) -> None:
        """Drop every cached discovery, so the next ``discover()`` reads the registry."""
        self._cache.invalidate()

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
        return _Found(tuple(kept), at, started)

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


def blocked_lock() -> NotImplementedError:
    """``discover(locked=True)``: pinned resolution waits on the lockfile."""
    return _verify.blocked(
        "discover(locked=True): resolving only what donkey.lock pins needs the lockfile "
        "(BG §2.7, donkey.tools.lock())."
    )

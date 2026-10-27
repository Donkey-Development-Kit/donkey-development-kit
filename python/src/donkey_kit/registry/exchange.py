"""Exchange discovery + the governed-only join (BG §2.7).

:meth:`ExchangeRegistry.search` reads Exchange's GraphQL ``assets`` query
(docs/verified-apis.md §7, #53). Every other method that requires a real
Anypoint endpoint is gated behind a verification-blocked error (verification
discipline, working instruction #2) rather than a fabricated request path. The
N+1-avoiding index design is documented in :meth:`warm` for the implementer who
fills it in after the Verification milestone.
"""

from __future__ import annotations

import json
import re
from fnmatch import fnmatchcase
from typing import Any

from ..core import _verify
from ..core.cache import TTLCache
from ..core.config import DonkeyConfig
from ..core.errors import RegistryError
from ..core.transport import DonkeyAsyncClient
from .criteria import GovernanceCriteria, GovernanceReport
from .models import AgentHandle, AssetRef, AssetType, McpServerHandle

__all__ = ["ExchangeRegistry"]

# docs/verified-apis.md §7 (#53): the Exchange Graph API search MuleSoft
# documents in "Searching the Graph API", plus the ``types``, ``limit`` and
# ``offset`` fields its ``SearchAsset`` input also takes.
_GRAPHQL_PATH = "/graph/api/v1/graphql"
_SEARCH_QUERY = (
    "query($query: SearchAsset) { assets(query: $query, latestVersionsOnly: true) "
    "{ groupId assetId version name type description tags { key value } } }"
)
# One page of ``assets``.
_PAGE_MAX = 250
_GLOB = re.compile(r"[*?\[]")
# The SDK's discovery types → Exchange's ``type`` values. ``a2a-agent`` has no
# Exchange type of its own: an A2A agent publishes as ``agent`` (#1260).
_EXCHANGE_TYPES: dict[str, tuple[str, ...]] = {
    "mcp": ("mcp",),
    "agent": ("agent",),
    "api": ("rest-api", "http-api", "soap-api"),
}
_SDK_TYPES: dict[str, AssetType] = {
    "mcp": "mcp",
    "agent": "agent",
    "rest-api": "api",
    "http-api": "api",
    "soap-api": "api",
}


class ExchangeRegistry:
    """The Exchange client behind ``donkey.registry``: search, resolve and explain assets (BG §2.7).

    Uses the control-plane credential and caches lookups for
    ``registry_cache_ttl_s`` seconds (``DONKEY_NO_CACHE=1`` bypasses the cache).
    :meth:`search` reads Exchange; the methods that need an asset-resolution or
    API Manager endpoint raise ``NotImplementedError`` for now
    (docs/verified-apis.md §7).

    Args:
        cfg: The resolved configuration.
        http_client: The control-plane client.

    Docs: https://docs.donkey-kit.dev/tool-access/discovery
    """

    def __init__(self, cfg: DonkeyConfig, http_client: DonkeyAsyncClient) -> None:
        self._cfg = cfg
        self._http = http_client
        self._index_cache: TTLCache[object] = TTLCache(ttl_s=cfg.registry_cache_ttl_s)

    async def search(
        self,
        *,
        query: str | None = None,
        asset_types: list[AssetType] | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        environment: str | None = None,
        governed: bool | GovernanceCriteria | None = None,
        limit: int = 50,
    ) -> list[AssetRef]:
        """Search Exchange for assets matching the given filters.

        Exchange matches a plain ``query`` itself: whole words and word prefixes
        of the name, asset ID or description, ignoring case. A ``query`` with
        ``*``, ``?`` or ``[`` is a glob over those fields, matched here, because
        Exchange does not match inside words. ``asset_types`` and ``tags`` are
        Exchange filters; an asset matches when it has every tag, ignoring case.
        A tag is one set in Exchange or at publish, not one Exchange derives
        (those have a key). Each asset comes back at its latest version.
        Results are cached for ``registry_cache_ttl_s`` seconds.

        Raises:
            ConfigError: The control-plane settings are missing.
            ValueError: ``limit`` is below 1.
            RegistryError: Exchange refused the query.
            NotImplementedError: ``domain``, ``environment``, ``governed`` or the
                ``a2a-agent`` type was given; see docs/verified-apis.md §7.
        """
        cfg = self._cfg.validated(need="control_plane")
        if limit < 1:
            raise ValueError(f"limit must be at least 1, not {limit}.")
        if domain is not None:
            raise _verify.blocked(
                "search(domain=): Exchange's assets query has no domain filter, and "
                "where a domain lives on an asset is unconfirmed (docs/verified-apis.md §7)."
            )
        if governed or environment is not None:
            raise _verify.blocked(
                "search(governed=, environment=): the governed-state join over API "
                "Manager instances (the Verification milestone); see warm()."
            )
        if asset_types and "a2a-agent" in asset_types:
            raise _verify.blocked(
                "search(asset_types=['a2a-agent']): Exchange has no a2a-agent type; an "
                "A2A agent publishes as 'agent' (docs/verified-apis.md §7)."
            )
        glob = query.lower() if query and _GLOB.search(query) else None
        search: dict[str, Any] = {"organizationIds": [cfg.org_id]}
        if query and glob is None:
            search["searchTerm"] = query
        if asset_types:
            search["types"] = [t for sdk in asset_types for t in _EXCHANGE_TYPES[sdk]]
        if tags:
            search["tags"] = [{"value": tag} for tag in tags]
        wanted = {tag.lower() for tag in tags or ()}
        key = "search:" + json.dumps([search, glob, limit], sort_keys=True)
        cached = self._index_cache.get(key)
        if isinstance(cached, tuple):
            return list(cached)

        found: list[AssetRef] = []
        offset = 0
        page = min(limit, _PAGE_MAX) if glob is None else _PAGE_MAX
        while len(found) < limit:
            assets = await self._assets({**search, "limit": page, "offset": offset})
            for asset in assets:
                ref = _asset_ref(asset)
                if wanted <= {tag.lower() for tag in ref.tags} and _globbed(ref, glob):
                    found.append(ref)
            if len(assets) < page:
                break
            offset += page
        refs = tuple(found[:limit])
        self._index_cache.set(key, refs)
        return list(refs)

    async def _assets(self, search: dict[str, Any]) -> list[dict[str, Any]]:
        response = await self._http.post(
            self._cfg.control_plane_url.rstrip("/") + _GRAPHQL_PATH,
            json={"query": _SEARCH_QUERY, "variables": {"query": search}},
        )
        if not response.is_success:
            raise RegistryError(
                f"Exchange refused the asset search: HTTP {response.status_code} "
                f"{response.text[:300]}"
            )
        body = response.json()
        errors = body.get("errors") or ()
        if errors:
            raise RegistryError(
                "Exchange asset search failed: " + "; ".join(str(e.get("message")) for e in errors)
            )
        assets = (body.get("data") or {}).get("assets") or ()
        return [a for a in assets if isinstance(a, dict)]

    async def resolve_mcp(self, ref: AssetRef | str) -> McpServerHandle:
        """Resolve an MCP server asset to a connectable handle.

        Raises:
            ValueError: ``ref`` is not ``group_id/asset_id/version``.
            NotImplementedError: Otherwise, for now; see docs/verified-apis.md §7.
        """
        AssetRef.parse(ref)  # validate shape now; resolution needs verified API
        raise _verify.blocked(
            "Exchange asset-resolution API for MCP servers (docs/verified-apis.md §7)."
        )

    async def resolve_agent(self, ref: AssetRef | str) -> AgentHandle:
        """Resolve an A2A agent asset to a handle.

        Raises:
            ValueError: ``ref`` is not ``group_id/asset_id/version``.
            NotImplementedError: Otherwise, for now; see docs/verified-apis.md §7.
        """
        AssetRef.parse(ref)
        raise _verify.blocked(
            "Exchange asset-resolution API for A2A agents (docs/verified-apis.md §7)."
        )

    async def explain(
        self, ref: AssetRef | str, *, criteria: GovernanceCriteria
    ) -> GovernanceReport:
        """Explain why an asset is (not) governed.

        First-class, documented, referenced in the empty-result warning — without
        it, ``governed=True`` returning empty is indistinguishable from a broken
        credential. Blocked until the API Manager / ruleset read APIs are
        verified (the Verification milestone).
        """

        AssetRef.parse(ref)
        raise _verify.blocked(
            "governed-state join: per-instance 'deployed' readability and ruleset results API (the "
            "Verification milestone). Until verified, explain() cannot produce real Check rows; "
            "see registry/criteria.py for the pure evaluation logic."
        )

    async def warm(self, *, environment: str | None = None) -> None:
        """Build the in-memory governance index at startup.

        Design (fill in after the Verification milestone):
          1. ONE call to list all API Manager instances for (org, environment);
             index by (groupId, assetId, version) AND (groupId, assetId).
          2. Bulk policies call if one exists (verify the Verification milestone), else
             per-candidate.
          3. Apply Exchange-side filters (tags/lifecycle/type) BEFORE any API
             Manager calls to shrink the candidate set.
          4. Cache the whole index under registry_cache_ttl_s, keyed by env.
        """

        raise _verify.blocked(
            "API Manager instance-list + bulk-policy APIs for the governed-state "
            "index (the Verification milestone)."
        )

    def refresh(self) -> None:
        """Drop every cached registry lookup."""
        self._index_cache.invalidate()


def _asset_ref(asset: dict[str, Any]) -> AssetRef:
    return AssetRef(
        group_id=str(asset.get("groupId")),
        asset_id=str(asset.get("assetId")),
        version=str(asset.get("version")),
        name=asset.get("name"),
        type=_SDK_TYPES.get(str(asset.get("type"))),
        # A tag with a key is one Exchange derives, not one set on the asset.
        tags=tuple(
            str(tag.get("value"))
            for tag in asset.get("tags") or ()
            if isinstance(tag, dict) and tag.get("key") is None
        ),
        description=asset.get("description") or None,
    )


def _globbed(ref: AssetRef, glob: str | None) -> bool:
    """Whether ``ref``'s asset ID, name or description matches ``glob``, ignoring case."""
    if glob is None:
        return True
    fields = (ref.asset_id, ref.name or "", ref.description or "")
    return any(fnmatchcase(value.lower(), glob) for value in fields)

"""Exchange discovery + the governed-only join (BG §2.7).

The public signatures are fixed here so callers and tests can be written now.
Every method that requires a real Anypoint endpoint is gated behind a
verification-blocked error (verification discipline, working instruction #2) rather than a
fabricated request path. The N+1-avoiding index design is documented in
:meth:`warm` for the implementer who fills it in after M0.
"""

from __future__ import annotations

from ..core import _verify
from ..core.cache import TTLCache
from ..core.config import DonkeyConfig
from ..core.transport import DonkeyAsyncClient
from .governance import GovernanceCriteria, GovernanceReport
from .models import AgentHandle, AssetRef, AssetType, McpServerHandle


class ExchangeRegistry:
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
        self._cfg.validated(need="control_plane")
        raise _verify.blocked(
            "Exchange search API (endpoint, query params, response shape — "
            "docs/verified-apis.md §1/§7). Confirm against a sandbox and capture "
            "a fixture (BG §1.5) before implementing search()."
        )

    async def resolve_mcp(self, ref: AssetRef | str) -> McpServerHandle:
        AssetRef.parse(ref)  # validate shape now; resolution needs verified API
        raise _verify.blocked(
            "Exchange asset-resolution API for MCP servers (docs/verified-apis.md §7)."
        )

    async def resolve_agent(self, ref: AssetRef | str) -> AgentHandle:
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
            "see registry/governance.py for the pure evaluation logic."
        )

    async def warm(self, *, environment: str | None = None) -> None:
        """Build the in-memory governance index at startup.

        Design (fill in after M0):
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
        self._index_cache.invalidate()

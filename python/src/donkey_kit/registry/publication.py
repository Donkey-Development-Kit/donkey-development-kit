"""Publication — registering code-first assets into Exchange (BG §2.5).

Symmetric with governed-only discovery: one declarative object, three verbs, "declare in code, apply
in CI, verify at runtime". The symmetry breaks at runtime (BG §2.5): there is NO
runtime ``publish()`` — it would be actively harmful (immutable versions, catalog
reflecting process starts, privilege escalation, no review). The runtime verb is
:meth:`verify` (read-only drift check).

Verbs (BG §2.5):
  * ``preview()`` — laptop. Renders the entry as it would appear. Writes nothing.
  * ``export()``  — laptop. Compiles to a CI-publishable artifact (format unresolved,
    blocked). CI publishes on merge.
  * ``verify()``  — runtime, READ-ONLY. Reads the published ``mcp-metadata``
    tools, lists the live server's tools through its gateway, compares
    (:func:`tool_drift`); for an A2A agent, the published card against the
    card its gateway instance serves (:func:`card_drift`). Warns on a
    mismatch, or raises PublicationDrift.

The CI side of ``donkey publish --if-changed`` is here too: :func:`content_digest`
(pure, implemented) and :func:`publish_if_changed` (blocked). They moved from the
removed ``provisioning/`` package (#730). ``donkey publish`` itself publishes a
scanned repository's MCP servers through
:mod:`~donkey_kit.registry.exchange_publish` (#218), not through a
:class:`Publication`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from ..core import _verify
from ..core.errors import PublicationDrift
from .exchange import ExchangeRegistry

__all__ = [
    "Contact",
    "DescriptionIssue",
    "LiveAssets",
    "Publication",
    "PublicationAssetType",
    "PublicationSource",
    "VersionStrategy",
    "card_drift",
    "check_description_quality",
    "content_digest",
    "publish_if_changed",
    "tool_drift",
]

_log = logging.getLogger(__name__)


class PublicationAssetType(Enum):
    """SDK publication categories. The ledger records ``mcp`` and ``agent`` as
    Exchange types, each confirmed by a publish, and ``a2a-agent`` as not an
    Exchange type: an A2A agent publishes as ``agent`` (docs/verified-apis.md
    §7). ``api`` stands for the REST, HTTP and SOAP API types."""

    MCP_SERVER = "mcp"
    A2A_AGENT = "a2a-agent"
    AGENT = "agent"
    API = "api"


class VersionStrategy(Enum):
    """How a :class:`Publication` picks its version; ``PINNED`` (the default) never bumps."""

    PINNED = "pinned"  # default — no implicit bumps in a shared catalog
    FROM_PACKAGE = "from-package"
    SEMANTIC_AUTO = "semantic-auto"


@dataclass(frozen=True)
class Contact:
    """The owning team and contact email shown on a published Exchange entry."""

    team: str
    email: str


@dataclass(frozen=True)
class DescriptionIssue:
    """A tool description that is missing, tautological or too short."""

    tool: str
    kind: str  # "missing" | "tautological" | "too-short"
    detail: str


class LiveAssets(Protocol):
    """The live side :meth:`Publication.verify` compares against, read through
    the asset's gateway instance (BG §2.5). ``donkey.tools`` is one: the tools
    layer sits above this one, so it is reached through this protocol (§1.1)."""

    async def live_tools(self, ref: str) -> Sequence[Mapping[str, Any]]:
        """The tools MCP server ``ref`` lists now."""
        ...

    async def live_card(self, ref: str) -> Mapping[str, Any]:
        """The card A2A agent ``ref`` serves now."""
        ...


class PublicationSource(Protocol):
    """What :meth:`Publication.verify` reads: the published side from
    ``registry`` and the live side from ``tools``. A ``Donkey`` is one."""

    @property
    def registry(self) -> ExchangeRegistry:
        """The Exchange registry the published descriptors are read from."""
        ...

    @property
    def tools(self) -> LiveAssets:
        """The live side, read through the gateway."""
        ...


@dataclass(frozen=True)
class Publication:
    """A code-first asset to register in Exchange (BG §2.5).

    There is no runtime ``publish()``: :meth:`preview` and :meth:`export` run on
    a laptop, CI publishes the exported spec, and :meth:`verify` is the read-only
    runtime drift check. See the module docstring.

    Docs: https://docs.donkey-kit.dev/publishing
    """

    asset_type: PublicationAssetType
    group_id: str
    asset_id: str
    version: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    categories: dict[str, str] = field(default_factory=dict)
    contact: Contact | None = None
    descriptor: str = "auto"  # "auto" | "auto:live" | "auto:check"
    docs: list[tuple[str, str]] = field(default_factory=list)
    endpoint: str | None = None
    governance: Any | None = None
    version_strategy: VersionStrategy = VersionStrategy.PINNED

    # ---- verb: preview (laptop) -------------------------------------------
    async def preview(self, donkey: Any) -> str:  # noqa: ANN401 - Donkey is a higher layer
        """Render the Exchange entry as it would appear (BG §2.5). Blocked until
        descriptor derivation + Exchange render shape are verified (BG §2.5)."""
        raise _verify.blocked(
            "descriptor derivation (BG §2.5) + Exchange entry render (BG §2.5). The "
            "description-quality report (check_description_quality) is implemented "
            "and should run here (BG §2.5)."
        )

    # ---- verb: export (laptop) --------------------------------------------
    def export(self, path: str | Path | None = None) -> str:
        """Compile to a CI-publishable artifact (blocked: the format is unresolved)."""
        raise _verify.blocked(
            "Publication.export() would emit a donkey.yaml fragment, but the SDK no longer "
            "defines that spec format: it went with the refused provisioning control plane "
            "(#730). The export mapping remains "
            "unresolved: docs/verified-apis.md §5 confirms an Agent Network Maven-project + CLI "
            "flow but not whether export() wraps that toolchain or emits its project layout "
            "(the Verification milestone). This surface does not add a provisioning control "
            "plane competing with API Manager or Terraform."
        )

    # ---- verb: verify (runtime, READ-ONLY) --------------------------------
    async def verify(
        self, donkey: PublicationSource, *, raise_on_drift: bool = False
    ) -> tuple[str, ...]:
        """Compare the live asset against what this version was published with
        in Exchange (BG §2.5). Read-only: nothing is written anywhere.

        For an MCP server, the published side is the ``tools`` of the version's
        ``mcp-metadata`` file, ``donkey.registry.published_tools()``
        (docs/verified-apis.md §7). The live side is the server's ``tools/list``
        through its gateway instance, ``donkey.tools.live_tools()``, after the
        same governance check and with the same credential as a ``donkey.tools``
        binding (§9). A tool added or removed, a changed
        description, or a changed set of parameter names is drift. Parameter
        types are not compared: ``donkey publish`` records only the bare
        annotations it can read without importing the code.

        For an A2A agent (``AGENT`` or ``A2A_AGENT``), the published side is the
        version's agent card file (``donkey.registry.published_card()``), and the
        live side the card its gateway instance serves at
        ``/.well-known/agent-card.json`` (``donkey.tools.live_card()``), read
        after the same governance check and with the same credential (#1354).
        The comparison is :func:`card_drift`.

        Defaults to warn-and-continue: a drifted catalog must be loud, but must
        not take down production traffic. Each difference is logged as a
        warning, and the differences are returned (empty when the two agree).

        Args:
            donkey: The ``Donkey`` whose registry and gateway credential to use
                (any :class:`PublicationSource`).
            raise_on_drift: Raise :class:`~donkey_kit.core.errors.PublicationDrift`
                instead of warning.

        Raises:
            PublicationDrift: ``raise_on_drift`` is set and the two differ.
            NotImplementedError: The asset is an ``API``: only MCP servers and
                A2A agents are verified.
            RegistryError: Exchange has no such version or no ``mcp-metadata``
                or card file for it, the asset does not resolve to one gateway
                instance, or an agent is not governed (nothing is sent to it).
            UngovernedMcpServer: The server is not governed, so no credential
                is sent to it (ADR 0017).
        """
        ref = f"{self.group_id}/{self.asset_id}/{self.version}"
        if self.asset_type is PublicationAssetType.MCP_SERVER:
            published = await donkey.registry.published_tools(ref)
            differences = tool_drift(published, await donkey.tools.live_tools(ref))
        elif self.asset_type in (PublicationAssetType.AGENT, PublicationAssetType.A2A_AGENT):
            card = await donkey.registry.published_card(ref)
            differences = card_drift(card, await donkey.tools.live_card(ref))
        else:
            raise _verify.blocked(
                f"Publication.verify() for a {self.asset_type.value!r} asset: only MCP "
                "servers and A2A agents are verified (BG §2.5, docs/verified-apis.md §7)."
            )
        if differences and raise_on_drift:
            raise PublicationDrift(
                f"{ref} no longer matches its Exchange descriptor: " + "; ".join(differences)
            )
        for difference in differences:
            _log.warning("Publication drift on %s: %s", ref, difference)
        return differences


def tool_drift(
    published: Sequence[Mapping[str, Any]], live: Sequence[Mapping[str, Any]]
) -> tuple[str, ...]:
    """What differs between two tool lists (``name``, ``description``,
    ``inputSchema``), one line per difference, sorted by tool name (BG §2.5).
    Pure, implemented now.

    Compares the tool names, each tool's description (stripped; a missing one
    equals an empty one) and the names of its ``inputSchema`` properties.
    """
    before = {str(t.get("name")): t for t in published}
    after = {str(t.get("name")): t for t in live}
    differences: list[str] = []
    for name in sorted(before.keys() | after.keys()):
        if name not in after:
            differences.append(f"tool {name} was published but the server no longer lists it")
            continue
        if name not in before:
            differences.append(f"tool {name} is live but was not published")
            continue
        if _text(before[name].get("description")) != _text(after[name].get("description")):
            differences.append(f"tool {name}: the description changed")
        was, now = _params(before[name]), _params(after[name])
        if was != now:
            change = [f"+{p}" for p in sorted(now - was)] + [f"-{p}" for p in sorted(was - now)]
            differences.append(f"tool {name}: the parameters changed ({', '.join(change)})")
    return tuple(differences)


def card_drift(published: Mapping[str, Any], live: Mapping[str, Any]) -> tuple[str, ...]:
    """What differs between two A2A agent cards, one line per difference (BG §2.5,
    #1354). Pure.

    Compares the card's ``description`` and its ``defaultInputModes`` and
    ``defaultOutputModes`` (as sets), then the skills by ``id``: one added or
    removed, and each one's ``description`` (stripped; a missing one equals an
    empty one). Everything else is left out: ``url`` and ``supportedInterfaces``
    are where the agent is served, which differs between the published card and
    the deployed agent; ``version`` and ``protocolVersion`` are the agent's own
    release and the card schema; and the security fields are the upstream's,
    not the gateway's (docs/verified-apis.md §7).
    """
    differences: list[str] = []
    if _text(published.get("description")) != _text(live.get("description")):
        differences.append("the card description changed")
    for key, label in (("defaultInputModes", "input"), ("defaultOutputModes", "output")):
        was, now = _modes(published.get(key)), _modes(live.get(key))
        if was != now:
            change = [f"+{m}" for m in sorted(now - was)] + [f"-{m}" for m in sorted(was - now)]
            differences.append(f"the default {label} modes changed ({', '.join(change)})")
    before, after = _skills(published), _skills(live)
    for skill in sorted(before.keys() | after.keys()):
        if skill not in after:
            differences.append(f"skill {skill} was published but the agent no longer serves it")
        elif skill not in before:
            differences.append(f"skill {skill} is live but was not published")
        elif _text(before[skill].get("description")) != _text(after[skill].get("description")):
            differences.append(f"skill {skill}: the description changed")
    return tuple(differences)


def _modes(value: object) -> frozenset[str]:
    return frozenset(str(m) for m in value) if isinstance(value, list) else frozenset()


def _skills(card: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    skills = card.get("skills")
    if not isinstance(skills, list):
        return {}
    return {str(s.get("id")): s for s in skills if isinstance(s, dict)}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _params(tool: Mapping[str, Any]) -> frozenset[str]:
    schema = tool.get("inputSchema")
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    return frozenset(properties) if isinstance(properties, Mapping) else frozenset()


_NORMALISE = re.compile(r"[_\W]+")


def check_description_quality(
    tools: list[tuple[str, str | None]], *, min_len: int = 12
) -> list[DescriptionIssue]:
    """Fail-worthy description problems (BG §2.5). Pure, implemented now.

    A description missing, or equal to the identifier (after normalising
    underscores/case), or shorter than ``min_len`` is a FAILURE, not a warning —
    a tautological description makes a useless tool look documented.
    """

    issues: list[DescriptionIssue] = []
    for name, desc in tools:
        if not desc or not desc.strip():
            issues.append(DescriptionIssue(name, "missing", "no description"))
            continue
        norm_name = _NORMALISE.sub("", name).lower()
        norm_desc = _NORMALISE.sub("", desc).lower()
        if norm_desc == norm_name:
            issues.append(
                DescriptionIssue(name, "tautological", f"description equals identifier {name!r}")
            )
        elif len(desc.strip()) < min_len:
            issues.append(DescriptionIssue(name, "too-short", f"description under {min_len} chars"))
    return issues


def content_digest(descriptor: dict[str, Any], metadata: dict[str, Any]) -> str:
    """Stable hash over the canonical descriptor + metadata (BG §2.5).

    Canonicalised with sorted keys so semantically-identical inputs hash equal,
    which is what makes ``--if-changed`` reliable: it prevents catalog spam (a
    new version on every merge).
    """

    canonical = json.dumps(
        {"descriptor": descriptor, "metadata": metadata},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


async def publish_if_changed(publication: Publication, donkey: object) -> str:
    """CI default (BG §2.5): compare digest against the latest published version;
    if identical, skip and exit zero. Blocked: a :class:`Publication`'s
    descriptor derivation is unverified (see :meth:`Publication.preview`). The
    publish mechanism and digest storage are verified, and ``donkey publish``
    uses them for scanned MCP servers (#218)."""
    raise _verify.blocked(
        "descriptor derivation for a Publication (BG §2.5). The Exchange publish and "
        "digest read-back are verified (docs/verified-apis.md §7); `donkey publish` "
        "publishes scanned MCP servers through donkey_kit.registry.exchange_publish. "
        "Never delete/overwrite; deprecate via metadata (BG §2.5)."
    )

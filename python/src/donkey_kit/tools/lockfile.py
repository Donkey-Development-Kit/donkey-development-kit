"""The ``donkey.lock`` lockfile: pins the tools the gateway lists (BG §2.7).

A lock records, per governed MCP server, the digest of every descriptor
``tools/list`` returned. It is keyed by the gateway-visible tool name, so under
Tool Mapping it pins the mapped name, never the upstream one, and under
Progressive Disclosure it pins the listed ``search_tools`` / ``invoke_tool``,
never what a search would return (docs/verified-apis.md §9, #1642).

The lock detects drift. It does not control access: the gateway's own policies
still enforce. Provisional (ADR 0020): may change in a minor release, with a
migration note.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..core.errors import ConfigError, DonkeyError
from ..registry.models import McpServerHandle

__all__ = [
    "LOCK_FILE",
    "LOCK_VERSION",
    "LockDrift",
    "LockedServer",
    "Lockfile",
    "ToolLockDrift",
    "build_lock",
    "compare_lock",
    "digest",
    "read_lock",
    "write_lock",
]

LOCK_FILE = "donkey.lock"
LOCK_VERSION = 1
_DIGEST = "sha256"
_MISSING_REMEDIATION = "Run donkey.tools.lock() to write the lockfile, then retry."


def digest(descriptor: Mapping[str, object]) -> str:
    """The sha256 hex digest of ``descriptor`` as canonical JSON.

    Keys are sorted, because the gateway's raw key order is not stable across
    calls (#1642), so the same descriptor always hashes the same."""
    canonical = json.dumps(descriptor, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LockedServer:
    """One pinned server: its coordinates and ``tools``, which maps each
    gateway-visible tool name to the digest of its descriptor."""

    group_id: str
    asset_id: str
    version: str
    tools: Mapping[str, str]


@dataclass(frozen=True)
class Lockfile:
    """The parsed ``donkey.lock``: pinned servers keyed ``group_id/asset_id``,
    so a version bump reads as drift and not as a new server."""

    servers: Mapping[str, LockedServer]


def _key(server: McpServerHandle) -> str:
    return f"{server.ref.group_id}/{server.ref.asset_id}"


def _tool_digests(server: McpServerHandle) -> dict[str, str]:
    return {str(tool.get("name")): digest(tool) for tool in server.tool_descriptors or ()}


def build_lock(servers: Sequence[McpServerHandle]) -> Lockfile:
    """Pin every tool each of ``servers`` lists.

    Raises:
        ValueError: A server's descriptors are not loaded; run ``describe()``.
    """
    pinned: dict[str, LockedServer] = {}
    for server in servers:
        if server.tool_descriptors is None:
            raise ValueError(
                f"{server.ref.coordinates}: tool descriptors are not loaded; "
                "call ToolSet.describe() first."
            )
        pinned[_key(server)] = LockedServer(
            group_id=server.ref.group_id,
            asset_id=server.ref.asset_id,
            version=server.ref.version,
            tools=_tool_digests(server),
        )
    return Lockfile(servers=pinned)


def write_lock(lock: Lockfile, path: Path) -> None:
    """Write ``lock`` to ``path`` as sorted, indented JSON with a trailing newline."""
    document = {
        "version": LOCK_VERSION,
        "digest": _DIGEST,
        "servers": {
            key: {
                "group_id": s.group_id,
                "asset_id": s.asset_id,
                "version": s.version,
                "tools": dict(s.tools),
            }
            for key, s in lock.servers.items()
        },
    }
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def read_lock(path: Path) -> Lockfile:
    """Read the lockfile at ``path``.

    Raises:
        ConfigError: The file is missing, is not valid JSON, has an unsupported
            ``version`` or ``digest``, or lacks a key.
    """

    def bad(problem: str) -> ConfigError:
        return ConfigError(
            f"Lockfile {path} is unusable: {problem}.",
            remediation="Re-run donkey.tools.lock() to write a fresh lockfile.",
        )

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(
            f"Lockfile {path} does not exist.", remediation=_MISSING_REMEDIATION
        ) from None
    except OSError as exc:
        raise bad(f"cannot be read ({exc.strerror or type(exc).__name__})") from None
    try:
        raw = json.loads(text)
    except ValueError:
        raise bad("not valid JSON") from None
    if not isinstance(raw, dict):
        raise bad("the top level is not an object")
    if raw.get("version") != LOCK_VERSION:
        raise bad(f"unsupported version {raw.get('version')!r} (this SDK reads {LOCK_VERSION})")
    if raw.get("digest") != _DIGEST:
        raise bad(f"unsupported digest {raw.get('digest')!r} (this SDK reads {_DIGEST!r})")
    servers = raw.get("servers")
    if not isinstance(servers, dict):
        raise bad("missing 'servers'")
    pinned: dict[str, LockedServer] = {}
    for key, entry in servers.items():
        try:
            tools = entry["tools"]
            if not isinstance(tools, dict):
                raise TypeError
            pinned[str(key)] = LockedServer(
                group_id=str(entry["group_id"]),
                asset_id=str(entry["asset_id"]),
                version=str(entry["version"]),
                tools={str(n): str(d) for n, d in tools.items()},
            )
        except (KeyError, TypeError):
            raise bad(f"server {key!r} is malformed") from None
    return Lockfile(servers=pinned)


@dataclass(frozen=True)
class LockDrift:
    """One difference between the lock and the gateway: the pinned ``server``
    key, the ``tool`` (``None`` for a whole server) and the ``kind``."""

    server: str
    tool: str | None
    kind: Literal["server_missing", "tool_missing", "changed"]


class ToolLockDrift(DonkeyError):
    """The gateway's tools no longer match ``donkey.lock`` (BG §2.7).

    Raised before any tool binds. ``drift`` lists every pinned server or tool
    that is missing or changed. A tool the lock does not know is not drift: it
    is dropped and reported in ``ToolSet.excluded`` as ``"unpinned"``.
    Provisional (ADR 0020)."""

    remediation: str = "Review the gateway change, then re-run donkey.tools.lock() to accept it."

    def __init__(self, message: str, *, drift: tuple[LockDrift, ...] = ()) -> None:
        super().__init__(message)
        self.drift = drift

    @classmethod
    def of(cls, drift: tuple[LockDrift, ...]) -> ToolLockDrift:
        """The error naming every entry of ``drift``."""
        lines = []
        for d in drift:
            if d.kind == "server_missing":
                lines.append(f"{d.server}: server not found")
            else:
                what = "was removed" if d.kind == "tool_missing" else "changed"
                lines.append(f"{d.server}: tool {d.tool!r} {what}")
        return cls("donkey.lock drift: " + "; ".join(lines) + ".", drift=drift)


def compare_lock(
    lock: Lockfile, servers: Sequence[McpServerHandle]
) -> tuple[tuple[LockDrift, ...], frozenset[tuple[str, str]]]:
    """Compare ``lock`` with ``servers``: the drift, and the unpinned
    ``(server_key, tool_name)`` pairs.

    A server whose descriptors are not loaded is checked for presence only."""
    present = {_key(s): s for s in servers}
    drift: list[LockDrift] = []
    unpinned: set[tuple[str, str]] = set()
    for key, pinned in lock.servers.items():
        server = present.get(key)
        if server is None:
            drift.append(LockDrift(key, None, "server_missing"))
            continue
        if server.tool_descriptors is None:
            continue
        live = _tool_digests(server)
        for name, pinned_digest in pinned.tools.items():
            if name not in live:
                drift.append(LockDrift(key, name, "tool_missing"))
            elif live[name] != pinned_digest:
                drift.append(LockDrift(key, name, "changed"))
        unpinned.update((key, name) for name in live if name not in pinned.tools)
    for key, server in present.items():
        if key not in lock.servers and server.tool_descriptors is not None:
            unpinned.update((key, name) for name in _tool_digests(server))
    return tuple(drift), frozenset(unpinned)

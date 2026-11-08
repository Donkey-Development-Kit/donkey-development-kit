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
import os
import re
import secrets
import stat
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
    "server_key",
    "write_lock",
]

LOCK_FILE = "donkey.lock"
LOCK_VERSION = 1
_DIGEST = "sha256"
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
# The descriptor fields a digest covers: the MCP contract a model sees. Fields an
# ``mcp`` release adds later (``icons``, ``execution``, ``_meta``) never move a digest.
DIGEST_FIELDS = ("name", "title", "description", "inputSchema", "outputSchema", "annotations")
_MISSING_REMEDIATION = "Run donkey.tools.lock() to write the lockfile, then retry."


def digest(descriptor: Mapping[str, object]) -> str:
    """The sha256 hex digest of ``descriptor`` as canonical JSON.

    Only the contract fields in ``DIGEST_FIELDS`` (those present) are hashed, so
    a field a newer ``mcp`` release adds does not change a digest. Keys are
    sorted, because the gateway's raw key order is not stable across calls
    (#1642), so the same descriptor always hashes the same."""
    covered = {k: descriptor[k] for k in DIGEST_FIELDS if k in descriptor}
    canonical = json.dumps(covered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
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
    """The parsed ``donkey.lock``: pinned servers keyed ``group_id/asset_id``.

    The version is recorded for information only. A new version that lists the
    same tools is not drift, and one that changes a tool is."""

    servers: Mapping[str, LockedServer]


def server_key(server: McpServerHandle) -> str:
    """The lock key of ``server``: ``group_id/asset_id``."""
    return f"{server.ref.group_id}/{server.ref.asset_id}"


def _tool_digests(server: McpServerHandle) -> dict[str, str]:
    return {str(tool.get("name")): digest(tool) for tool in server.tool_descriptors or ()}


def _strict_digests(server: McpServerHandle) -> dict[str, str]:
    digests: dict[str, str] = {}
    for tool in server.tool_descriptors or ():
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{server.ref.coordinates}: a listed tool has no name.")
        if name in digests:
            raise ValueError(
                f"{server.ref.coordinates}: duplicate tool name {name!r} in its listing."
            )
        digests[name] = digest(tool)
    return digests


def build_lock(servers: Sequence[McpServerHandle]) -> Lockfile:
    """Pin every tool each of ``servers`` lists.

    Raises:
        ValueError: A server's descriptors are not loaded (run ``describe()``),
            or its listing has a tool with no name or two tools of one name.
    """
    pinned: dict[str, LockedServer] = {}
    for server in servers:
        if server.tool_descriptors is None:
            raise ValueError(
                f"{server.ref.coordinates}: tool descriptors are not loaded; "
                "call ToolSet.describe() first."
            )
        pinned[server_key(server)] = LockedServer(
            group_id=server.ref.group_id,
            asset_id=server.ref.asset_id,
            version=server.ref.version,
            tools=_strict_digests(server),
        )
    return Lockfile(servers=pinned)


def write_lock(lock: Lockfile, path: Path) -> None:
    """Write ``lock`` to ``path`` as sorted, indented JSON with a trailing newline.

    The write is atomic: a temporary file beside ``path`` is renamed over it, so
    a failed write never leaves a truncated lockfile. An existing file keeps its
    permission bits; a new one gets ``0o666`` minus the umask.

    Raises:
        OSError: The file or its directory cannot be written.
    """
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
    text = json.dumps(document, sort_keys=True, indent=2) + "\n"
    # Not mkstemp: it forces 0600, which would make the lock unreadable to other
    # users. os.open applies the process umask itself (0o666 & ~umask) without
    # the process-global, racy os.umask() round-trip. O_BINARY (Windows only)
    # matches what mkstemp did, so the text wrapper below does not yield \r\r\n.
    temp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    handle = os.open(temp, flags, 0o666)
    try:
        try:
            existing: int | None = stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            existing = None
        if existing is not None and hasattr(os, "fchmod"):
            os.fchmod(handle, existing)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def read_lock(path: Path) -> Lockfile:
    """Read the lockfile at ``path``.

    Raises:
        ConfigError: The file is missing, is not valid JSON, has an unsupported
            ``version`` or ``digest``, lacks a key, has a malformed digest or
            has a server key that is not ``group_id/asset_id``.
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
    except UnicodeDecodeError:
        raise bad("not UTF-8") from None
    except OSError as exc:
        raise bad(f"cannot be read ({exc.strerror or type(exc).__name__})") from None
    try:
        raw = json.loads(text)
    except ValueError:
        raise bad("not valid JSON") from None
    if not isinstance(raw, dict):
        raise bad("the top level is not an object")
    if type(raw.get("version")) is not int or raw["version"] != LOCK_VERSION:
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
            locked = LockedServer(
                group_id=str(entry["group_id"]),
                asset_id=str(entry["asset_id"]),
                version=str(entry["version"]),
                tools={str(n): d for n, d in tools.items()},
            )
        except (KeyError, TypeError):
            raise bad(f"server {key!r} is malformed") from None
        if f"{locked.group_id}/{locked.asset_id}" != key:
            raise bad(f"server key {key!r} is not group_id/asset_id")
        for name, pinned_digest in locked.tools.items():
            if not isinstance(pinned_digest, str) or not _HEX_DIGEST.fullmatch(pinned_digest):
                raise bad(f"server {key!r} tool {name!r} has a malformed digest")
        pinned[key] = locked
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
    Provisional (ADR 0020). A root export by exception (ADR 0024)."""

    remediation: str = "Review the gateway change, then re-run donkey.tools.lock() to accept it."

    def __init__(
        self,
        message: str,
        *,
        drift: tuple[LockDrift, ...] = (),
        remediation: str | None = None,
    ) -> None:
        super().__init__(message, remediation=remediation)
        self.drift = drift

    @classmethod
    def of(cls, drift: tuple[LockDrift, ...]) -> ToolLockDrift:
        """The error naming every entry of ``drift``."""
        lines = []
        for d in drift:
            if d.kind == "server_missing":
                lines.append(
                    f"{d.server}: server not discovered (removed, no longer governed, "
                    "or outside this discover()'s tags / governed)"
                )
            else:
                what = "was removed" if d.kind == "tool_missing" else "changed"
                lines.append(f"{d.server}: tool {d.tool!r} {what}")
        return cls("donkey.lock drift: " + "; ".join(lines) + ".", drift=drift)


def compare_lock(
    lock: Lockfile, servers: Sequence[McpServerHandle], *, check_presence: bool = True
) -> tuple[tuple[LockDrift, ...], frozenset[tuple[str, str]]]:
    """Compare ``lock`` with ``servers``: the drift, and the unpinned
    ``(server_key, tool_name)`` pairs.

    A server whose descriptors are not loaded is checked for presence only. With
    ``check_presence=False`` a pinned server missing from ``servers`` is skipped,
    for a set already narrowed by ``filter(servers=...)``."""
    present = {server_key(s): s for s in servers}
    drift: list[LockDrift] = []
    unpinned: set[tuple[str, str]] = set()
    for key, pinned in lock.servers.items():
        server = present.get(key)
        if server is None:
            if check_presence:
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

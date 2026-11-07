"""The donkey.lock model, digest, file format and drift comparison (BG §2.7, #1646)."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from donkey_kit import ConfigError
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools.lockfile import (
    LockDrift,
    ToolLockDrift,
    build_lock,
    compare_lock,
    digest,
    read_lock,
    write_lock,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "mcp_policies"


def _catalog(name: str) -> tuple[dict[str, Any], ...]:
    capture = json.loads((_FIXTURES / f"{name}-list.json").read_text("utf-8"))
    payload = capture["body"].removeprefix("event: message\ndata: ").strip()
    return tuple(json.loads(payload)["result"]["tools"])


_KEY = "com.example/inventory"


def _server(catalog: tuple[dict[str, Any], ...] | None, version: str = "1.0.0") -> McpServerHandle:
    return McpServerHandle(
        ref=AssetRef("com.example", "inventory", version),
        endpoint_url="https://gateway.example.test/mcp/",
        tool_descriptors=catalog,
    )


def test_digest_ignores_key_order() -> None:
    assert digest({"a": 1, "b": {"x": 1, "y": 2}}) == digest({"b": {"y": 2, "x": 1}, "a": 1})


def test_digest_is_sha256_of_canonical_json() -> None:
    assert digest({"a": 1}) == hashlib.sha256(b'{"a":1}').hexdigest()


def test_build_lock_from_progressive_catalog() -> None:
    lock = build_lock([_server(_catalog("progressive"))])
    assert set(lock.servers[_KEY].tools) == {"search_tools", "invoke_tool"}


def test_build_lock_from_mapping_catalog() -> None:
    tools = build_lock([_server(_catalog("mapping"))]).servers[_KEY].tools
    assert "arithmetic" in tools
    assert "calculate" not in tools


def test_build_lock_requires_described_servers() -> None:
    with pytest.raises(ValueError, match="describe"):
        build_lock([_server(None)])


def test_round_trip(tmp_path: Path) -> None:
    lock = build_lock([_server(_catalog("progressive"))])
    path = tmp_path / "donkey.lock"
    write_lock(lock, path)
    assert read_lock(path) == lock
    text = path.read_text("utf-8")
    assert text.endswith("\n")
    assert text == json.dumps(json.loads(text), sort_keys=True, indent=2) + "\n"
    document = json.loads(text)
    assert document["version"] == 1
    assert document["digest"] == "sha256"


def test_read_missing_lock(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        read_lock(tmp_path / "donkey.lock")
    assert "donkey.tools.lock()" in exc.value.remediation


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        json.dumps({"version": 1, "digest": "sha256"}),
        json.dumps({"version": 2, "digest": "sha256", "servers": {}}),
        json.dumps({"version": 1, "digest": "md5", "servers": {}}),
        json.dumps({"version": 1, "digest": "sha256", "servers": {"a/b": {}}}),
    ],
)
def test_read_rejects(tmp_path: Path, text: str) -> None:
    path = tmp_path / "donkey.lock"
    path.write_text(text, "utf-8")
    with pytest.raises(ConfigError, match="donkey.lock"):
        read_lock(path)


# ---- drift (Task 2) ---------------------------------------------------------
def test_identical_catalog_no_drift() -> None:
    servers = [_server(_catalog("progressive"))]
    assert compare_lock(build_lock(servers), servers) == ((), frozenset())


def test_reordered_descriptor_no_drift() -> None:
    catalog = _catalog("mapping")
    lock = build_lock([_server(catalog)])
    reordered = tuple(dict(reversed(list(tool.items()))) for tool in copy.deepcopy(catalog))
    assert compare_lock(lock, [_server(reordered)]) == ((), frozenset())


def test_mapping_rename_is_drift() -> None:
    mapped = _catalog("mapping")
    original = tuple(
        {**tool, "name": "calculate"} if tool["name"] == "arithmetic" else tool for tool in mapped
    )
    lock = build_lock([_server(original)])
    drift, unpinned = compare_lock(lock, [_server(mapped)])
    assert LockDrift(_KEY, "calculate", "tool_missing") in drift
    assert (_KEY, "arithmetic") in unpinned


def test_description_change_is_drift() -> None:
    catalog = _catalog("mapping")
    lock = build_lock([_server(catalog)])
    changed = ({**catalog[0], "description": "different"}, *catalog[1:])
    drift, _ = compare_lock(lock, [_server(changed)])
    assert drift == (LockDrift(_KEY, str(catalog[0]["name"]), "changed"),)


def test_pinned_server_absent() -> None:
    lock = build_lock([_server(_catalog("mapping"))])
    assert compare_lock(lock, [])[0] == (LockDrift(_KEY, None, "server_missing"),)


def test_new_tool_is_unpinned_not_drift() -> None:
    catalog = _catalog("mapping")
    lock = build_lock([_server(catalog)])
    extra = (*catalog, {"name": "new_tool", "inputSchema": {}})
    assert compare_lock(lock, [_server(extra)]) == ((), frozenset({(_KEY, "new_tool")}))


def test_tool_lock_drift_message_names_every_entry() -> None:
    drift = (LockDrift(_KEY, "a", "tool_missing"), LockDrift("x/y", None, "server_missing"))
    exc = ToolLockDrift.of(drift)
    assert exc.drift == drift
    assert "'a'" in str(exc)
    assert "x/y" in str(exc)
    assert exc.remediation

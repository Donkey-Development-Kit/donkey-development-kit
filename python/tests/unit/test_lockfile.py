"""The donkey.lock model, digest, file format and drift comparison (BG §2.7, #1646)."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import sys
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
    server_key,
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
    assert digest({"name": "a"}) == hashlib.sha256(b'{"name":"a"}').hexdigest()


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


_posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


@_posix_only
def test_new_lock_honours_umask(tmp_path: Path) -> None:
    lock = build_lock([_server(_catalog("progressive"))])
    path = tmp_path / "donkey.lock"
    old = os.umask(0o022)
    try:
        write_lock(lock, path)
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


@_posix_only
def test_rewrite_preserves_existing_mode(tmp_path: Path) -> None:
    lock = build_lock([_server(_catalog("progressive"))])
    path = tmp_path / "donkey.lock"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o664)
    write_lock(lock, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o664
    assert read_lock(path) == lock


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


def _reversed(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _reversed(v) for k, v in reversed(list(value.items()))}
    if isinstance(value, list):
        return [_reversed(v) for v in value]
    return value


def test_reordered_descriptor_no_drift() -> None:
    catalog = _catalog("mapping")
    lock = build_lock([_server(catalog)])
    reordered = tuple(_reversed(copy.deepcopy(tool)) for tool in catalog)
    assert any("properties" in t.get("inputSchema", {}) for t in catalog)
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


def test_server_key() -> None:
    assert server_key(_server(None)) == _KEY


def test_version_bump_with_same_tools_is_no_drift() -> None:
    catalog = _catalog("mapping")
    lock = build_lock([_server(catalog, "1.0.0")])
    assert compare_lock(lock, [_server(catalog, "2.0.0")]) == ((), frozenset())
    assert lock.servers[_KEY].version == "1.0.0"


def test_compare_without_presence_ignores_absent_servers() -> None:
    lock = build_lock([_server(_catalog("mapping"))])
    assert compare_lock(lock, [], check_presence=False) == ((), frozenset())


def test_digest_ignores_fields_outside_the_contract() -> None:
    base = {"name": "t", "description": "d", "inputSchema": {"type": "object"}}
    assert digest({**base, "_meta": {"x": 1}, "icons": [], "execution": None}) == digest(base)
    assert digest({**base, "description": "other"}) != digest(base)
    assert digest({**base, "title": "T"}) != digest(base)


def test_digest_non_ascii_is_utf8_unescaped() -> None:
    descriptor = {"name": "t", "description": "café \u2713"}
    expected = json.dumps(descriptor, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert digest(descriptor) == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert "\\u" not in expected


def test_build_lock_refuses_duplicate_and_missing_names() -> None:
    dup = ({"name": "a"}, {"name": "a", "description": "x"})
    with pytest.raises(ValueError, match="inventory.*duplicate.*'a'"):
        build_lock([_server(dup)])
    with pytest.raises(ValueError, match="inventory.*no name"):
        build_lock([_server(({"description": "x"},))])


def test_read_rejects_non_utf8(tmp_path: Path) -> None:
    path = tmp_path / "donkey.lock"
    path.write_bytes(b"\xff")
    with pytest.raises(ConfigError, match="not UTF-8"):
        read_lock(path)


def _document(**over: Any) -> str:
    entry: dict[str, Any] = {
        "group_id": "com.example",
        "asset_id": "inventory",
        "version": "1",
        "tools": {"a": "0" * 64},
    }
    entry.update(over.pop("entry", {}))
    doc: dict[str, Any] = {
        "version": 1,
        "digest": "sha256",
        "servers": {over.pop("key", _KEY): entry},
    }
    doc.update(over)
    return json.dumps(doc)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"version": True}, "version"),
        ({"version": 1.0}, "version"),
        ({"entry": {"tools": {"a": "XYZ"}}}, "digest"),
        ({"entry": {"tools": {"a": "A" * 64}}}, "digest"),
        ({"entry": {"tools": {"a": 5}}}, "digest"),
        ({"key": "other/thing"}, "key"),
    ],
)
def test_read_validates_content(tmp_path: Path, kwargs: dict[str, Any], match: str) -> None:
    path = tmp_path / "donkey.lock"
    path.write_text(_document(**kwargs), "utf-8")
    with pytest.raises(ConfigError, match=match):
        read_lock(path)


def test_valid_document_reads(tmp_path: Path) -> None:
    path = tmp_path / "donkey.lock"
    path.write_text(_document(), "utf-8")
    assert read_lock(path).servers[_KEY].tools == {"a": "0" * 64}


def test_write_is_atomic_and_leaves_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    path = tmp_path / "donkey.lock"
    write_lock(build_lock([_server(_catalog("mapping"))]), path)
    before = path.read_text("utf-8")

    def boom(*_: object) -> None:
        raise OSError("disk")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_lock(build_lock([_server(_catalog("progressive"))]), path)
    assert path.read_text("utf-8") == before
    assert [p.name for p in tmp_path.iterdir()] == ["donkey.lock"]


def test_server_missing_message_is_not_misleading() -> None:
    exc = ToolLockDrift.of((LockDrift(_KEY, None, "server_missing"),))
    assert "no longer governed" in str(exc)
    assert "tags" in str(exc)

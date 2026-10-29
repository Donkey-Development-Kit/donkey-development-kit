"""The key sets of the CLI's ``--json`` documents (#1427), pinned by the
``test_cli_*`` tests. Changing a set here changes a public document: a key
removed or renamed bumps that document's format constant.
"""

from __future__ import annotations

from typing import Any

ISSUE_KEYS = {"kind", "subject", "path", "line", "detail"}

PLAN_KEYS = {"plan_format", "assets", "issues"}
PLAN_ASSET_KEYS = {
    "service_id",
    "type",
    "group_id",
    "asset_id",
    "version",
    "name",
    "description",
    "endpoint",
    "tools",
    "digest",
    "protocol_version",
    "provider",
    "documentation",
    "protocol",
}

PUBLISH_KEYS = {"publish_format", "assets", "issues"}
PUBLISH_ASSET_KEYS = PLAN_ASSET_KEYS | {"status", "detail"}

VERIFY_KEYS = {"verify_format", "assets", "issues"}
VERIFY_ASSET_KEYS = PLAN_ASSET_KEYS | {"status", "detail", "differences"}

EXPOSE_KEYS = {"expose_format", "ok", "dry_run", "url", "issues", "steps"}
EXPOSE_STEP_KEYS = {"name", "status", "detail", "remediation"}


def assert_shape(document: dict[str, Any], keys: set[str], items: str, item_keys: set[str]) -> None:
    """``document`` has exactly ``keys``, each of its ``items`` exactly
    ``item_keys`` and each issue exactly :data:`ISSUE_KEYS`; its format is 1."""
    assert set(document) == keys
    (format_key,) = (k for k in keys if k.endswith("_format"))
    assert document[format_key] == 1
    for item in document[items]:
        assert set(item) == item_keys
    for issue in document["issues"]:
        assert set(issue) == ISSUE_KEYS

"""The Exchange page ``donkey publish`` writes for an MCP asset (#1269, #1273):
the same sections for every asset, the declared documentation, each tool's
governance, plain table cells, and provenance with no group ID.
"""

from __future__ import annotations

from pathlib import Path

from donkey_kit.registry.exchange_page import PageSource, documentation_page
from donkey_kit.registry.publish_plan import plan_publication
from donkey_kit.registry.scan import scan

GROUP = "00000000-0000-4000-8000-306f0858eed3"

SOURCE = '''\
from donkey_kit import Donkey

donkey = Donkey.from_env()


@donkey.tool(server="sku-manager")
def lookup_sku(sku: str, mode: Literal['a'] | None) -> dict:
    """Look up a SKU's price and stock level by its code."""


@donkey.tool(server="sku-manager", approval="always", risk="low")
def ping() -> str:
    """Check that the server answers."""
'''

TOML = """\
[donkey.publication]
version = "1.1.0"
protocol_version = "2025-06-18"

[donkey.publication.assets.sku-manager]
name = "SKU Lookup"
description = "Price and stock by SKU."
documentation = "README.md"

[donkey.mcp_servers.sku-manager]
endpoint = "https://sku.example.invalid/mcp"
"""


def _page(tmp_path: Path, source: PageSource | None = None) -> str:
    (tmp_path / "app.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / ".donkey-kit.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "README.md").write_text("## Use it\n\nAsk about stock.\n", encoding="utf-8")
    result = scan(tmp_path)
    plan = plan_publication(result, tmp_path, default_group_id=GROUP)
    assert plan.issues == ()
    return documentation_page(plan.assets[0], result, source=source)


def test_the_page_opens_with_the_name_description_and_documentation(tmp_path: Path) -> None:
    page = _page(tmp_path)

    assert page.startswith(
        "# SKU Lookup\n\n## Overview\n\nPrice and stock by SKU.\n\n## Use it\n\nAsk about stock.\n"
    )
    sections = [line for line in page.splitlines() if line.startswith("## ")]
    assert sections == ["## Overview", "## Use it", "## Tools", "## Connecting", "## Provenance"]
    assert page.index("### lookup_sku") < page.index("### ping")
    assert "No parameters." in page
    assert "- **Endpoint:** <https://sku.example.invalid/mcp>" in page
    assert "- **MCP protocol version:** 2025-06-18" in page


def test_table_cells_are_one_line_with_pipes_escaped(tmp_path: Path) -> None:
    page = _page(tmp_path)

    assert "| mode | Literal['a'] \\| None |" in page
    assert "| sku | str |" in page


def test_provenance_links_the_commit_and_run_but_not_the_group(tmp_path: Path) -> None:
    repository = "https://github.example.invalid/acme/sku"
    source = PageSource(repository, "b" * 40, "main", repository + "/actions/runs/7")

    page = _page(tmp_path, source)

    assert "- **Asset:** sku-manager 1.1.0" in page
    assert f"- **Commit:** [{'b' * 12}]({repository}/commit/{'b' * 40}) on main" in page
    assert f"[this CI run]({repository}/actions/runs/7)" in page
    assert GROUP not in page


def test_each_tool_shows_the_governance_it_declares(tmp_path: Path) -> None:
    page = _page(tmp_path)

    ping = page[page.index("### ping") :]
    lookup = page[page.index("### lookup_sku") : page.index("### ping")]
    assert "Governed with approval: always, risk: low." in ping
    assert "Governed with" not in lookup


def test_a_missing_description_is_said_so(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / ".donkey-kit.toml").write_text(
        '[donkey.publication]\nversion = "1.1.0"\n', encoding="utf-8"
    )
    result = scan(tmp_path)
    plan = plan_publication(result, tmp_path, default_group_id=GROUP)

    page = documentation_page(plan.assets[0], result)

    assert "## Overview\n\nNo description is declared.\n" in page

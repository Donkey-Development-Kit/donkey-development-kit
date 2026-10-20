"""``donkey_kit.registry.scan`` (#217, BG §2.5): a static, deterministic model of
a repository's agents, tools and MCP servers.

Every fixture is written to ``tmp_path`` and scanned without being imported:
several of them would fail or have side effects if they ran.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from donkey_kit.registry.scan import SCAN_FORMAT, ScanResult, scan


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")
    return root


GOV = """\
    from donkey_kit import Donkey

    donkey = Donkey.from_env()
"""

MAIN = """\
    raise SystemExit("scan must never import this module")

    from .gov import donkey


    @donkey.tool(server="sku-manager")
    def lookup_sku(sku: str, *, region: str = "eu") -> dict:
        \"\"\"Look up a SKU's price and stock level by its code.\"\"\"


    @donkey.tool(approval="required", risk="financial")
    async def issue_refund(order_id: str, amount: float) -> None:
        \"\"\"Issue a refund for an order, up to the order total.\"\"\"


    @donkey.agent(name="support-triage", skills=["triage", "draft-reply"])
    async def handle(task):
        \"\"\"Triage a support ticket and draft a reply for a human to send.\"\"\"
"""


def _repo(tmp_path: Path) -> Path:
    return _write(tmp_path, {"app/__init__.py": "", "app/gov.py": GOV, "app/main.py": MAIN})


def _kinds(result: ScanResult) -> list[str]:
    return [i.kind for i in result.issues]


def test_scan_finds_agents_tools_and_servers_without_importing(tmp_path: Path) -> None:
    result = scan(_repo(tmp_path))

    (agent,) = result.agents
    assert (agent.name, agent.qualname, agent.path, agent.is_async) == (
        "support-triage",
        "handle",
        "app/main.py",
        True,
    )
    assert agent.skills == ("triage", "draft-reply")
    assert [t.name for t in result.tools] == ["lookup_sku", "issue_refund"]
    lookup, refund = result.tools
    assert lookup.server == "sku-manager"
    assert [(p.name, p.annotation) for p in lookup.parameters] == [
        ("sku", "str"),
        ("region", "str"),
    ]
    assert (refund.server, refund.approval, refund.risk, refund.is_async) == (
        None,
        "required",
        "financial",
        True,
    )
    assert [(s.id, s.tools) for s in result.mcp_servers] == [("sku-manager", ("lookup_sku",))]
    assert result.unattributed == (refund,)
    assert result.issues == ()


def test_scan_output_is_byte_identical_across_runs(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    first, second = scan(root).to_json(), scan(root).to_json()

    assert first == second
    assert first.endswith("\n")
    data = json.loads(first)
    assert data["scan_format"] == SCAN_FORMAT
    assert data["unattributed"] == ["issue_refund"]
    assert data["digest"] == scan(root).digest()


def test_digest_ignores_source_locations_but_not_content(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    before = scan(root).digest()

    main = root / "app/main.py"
    main.write_text("# moved down\n\n" + main.read_text(), encoding="utf-8")
    assert scan(root).digest() == before

    main.write_text(main.read_text().replace("price and stock", "stock"), encoding="utf-8")
    assert scan(root).digest() != before


@pytest.mark.parametrize(
    "imports",
    [
        "from donkey_kit import Donkey\n",
        "import donkey_kit\n",
        "from donkey_kit import Donkey as D\n",
    ],
)
def test_the_donkey_class_and_its_instances_are_receivers(tmp_path: Path, imports: str) -> None:
    cls = {"from donkey_kit import Donkey\n": "Donkey", "import donkey_kit\n": "donkey_kit.Donkey"}
    ref = cls.get(imports, "D")
    source = (
        imports
        + f"\ndk = {ref}()\n\n"
        + f'@{ref}.tool(server="a")\ndef one():\n    """Look up one record by its ID."""\n\n'
        + '@dk.tool(server="a")\ndef two():\n    """Look up two records by their IDs."""\n'
    )
    result = scan(_write(tmp_path, {"m.py": source}))

    assert [t.name for t in result.tools] == ["one", "two"]


def test_foreign_tool_decorators_are_ignored(tmp_path: Path) -> None:
    source = """\
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("other")


        @mcp.tool()
        def not_ours():
            \"\"\"Belongs to another framework's server.\"\"\"
    """
    result = scan(_write(tmp_path, {"m.py": source}))

    assert result == ScanResult()


def test_instances_resolve_through_re_exports(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "src/pkg/__init__.py": "from .gov import donkey\n",
            "src/pkg/gov.py": GOV,
            "src/pkg/sub/__init__.py": "",
            "src/pkg/sub/tools.py": (
                "from pkg import donkey as dk\n\n"
                '@dk.tool(server="crm")\ndef find():\n    """Find a customer by email address."""\n'
            ),
            "src/pkg/sub/deep.py": (
                "from .. import donkey\n\n"
                '@donkey.tool(server="crm")\ndef list_all():\n    """List every customer."""\n'
            ),
        },
    )
    result = scan(root)

    assert [(t.path, t.name) for t in result.tools] == [
        ("src/pkg/sub/deep.py", "list_all"),
        ("src/pkg/sub/tools.py", "find"),
    ]
    assert result.mcp_servers[0].tools == ("find", "list_all")


def test_qualnames_follow_classes_and_locals(tmp_path: Path) -> None:
    source = """\
        from donkey_kit import Donkey


        class Tools:
            @Donkey.tool(server="inv")
            def count(self, sku: str, *rest, **extra):
                \"\"\"Count the units of a SKU in stock.\"\"\"


        def factory():
            @Donkey.tool(server="inv")
            def inner(x):
                \"\"\"An inner tool that a factory builds.\"\"\"
    """
    result = scan(_write(tmp_path, {"m.py": source}))

    count, inner = result.tools
    assert count.qualname == "Tools.count"
    assert [p.name for p in count.parameters] == ["sku", "*rest", "**extra"]
    assert inner.qualname == "factory.<locals>.inner"
    assert [p.name for p in inner.parameters] == ["x"]


def test_description_issues(tmp_path: Path) -> None:
    source = """\
        from donkey_kit import Donkey


        @Donkey.tool(server="a")
        def missing():
            pass


        @Donkey.tool(server="a")
        def issue_refund():
            \"\"\"issue_refund\"\"\"
    """
    result = scan(_write(tmp_path, {"m.py": source}))

    assert [(i.kind, i.subject) for i in result.issues] == [
        ("missing", "tool missing"),
        ("tautological", "tool issue_refund"),
    ]


def test_invalid_and_non_literal_arguments_are_reported(tmp_path: Path) -> None:
    source = """\
        from donkey_kit import Donkey

        SERVER = "a"


        @Donkey.tool(server="Bad_ID")
        def bad():
            \"\"\"A tool with an invalid server ID.\"\"\"


        @Donkey.tool(server=SERVER)
        def dynamic():
            \"\"\"A tool whose server is a variable.\"\"\"


        @Donkey.agent(name="Nope")
        def bad_agent():
            \"\"\"An agent with an invalid name.\"\"\"


        @Donkey.agent()
        def nameless():
            \"\"\"An agent with no name at all.\"\"\"


        @Donkey.agent(name="ok", skills=["a", "a"])
        def repeats():
            \"\"\"An agent that repeats a skill ID.\"\"\"
    """
    result = scan(_write(tmp_path, {"m.py": source}))

    assert _kinds(result) == [
        "invalid-service-id",
        "non-literal-argument",
        "invalid-service-id",
        "invalid-service-id",
        "invalid-skills",
    ]
    assert [t.server for t in result.tools] == [None, None]
    (agent,) = result.agents
    assert (agent.name, agent.skills) == ("ok", ())


def test_duplicate_service_ids_and_tools(tmp_path: Path) -> None:
    source = """\
        from donkey_kit import Donkey


        @Donkey.agent(name="crm")
        def agent():
            \"\"\"An agent that clashes with a server ID.\"\"\"


        @Donkey.tool(server="crm")
        def find():
            \"\"\"Find a customer by email address.\"\"\"


        class Other:
            @Donkey.tool(server="crm")
            def find(self):
                \"\"\"Find a customer by phone number.\"\"\"
    """
    result = scan(_write(tmp_path, {"m.py": source}))

    assert [(i.kind, i.subject) for i in result.issues] == [
        ("duplicate-service-id", "agent crm"),
        ("duplicate-tool", "tool find"),
        ("duplicate-tool", "tool Other.find"),
    ]


def test_unparseable_files_are_issues_not_crashes(tmp_path: Path) -> None:
    root = _write(tmp_path, {"broken.py": "def (:\n"})
    (root / "latin.py").write_bytes(b"x = '\xff'\n")

    result = scan(root)

    assert [(i.kind, i.path) for i in result.issues] == [
        ("syntax-error", "broken.py"),
        ("unreadable", "latin.py"),
    ]


def test_tool_caches_and_virtualenvs_are_not_walked(tmp_path: Path) -> None:
    tool = 'from donkey_kit import Donkey\n\n@Donkey.tool(server="a")\ndef t():\n    """Tool."""\n'
    root = _write(
        tmp_path,
        {f"{d}/m.py": tool for d in (".venv", "venv", "node_modules", "__pycache__", ".git")},
    )

    assert scan(root) == ScanResult()


def test_a_single_file_can_be_scanned(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    result = scan(root / "app/gov.py")

    assert result == ScanResult()


def test_a_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="nothing to scan"):
        scan(tmp_path / "absent")

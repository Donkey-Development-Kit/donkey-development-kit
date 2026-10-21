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


# How an MCP server is reached is declared in .donkey-kit.toml, never read from
# code (#1193).

SERVERS = """\
    from donkey_kit import Donkey


    @Donkey.tool(server="hr-tools")
    def find_employee(email: str):
        \"\"\"Find an employee by their work email address.\"\"\"


    @Donkey.tool(server="sku-manager")
    def lookup_sku(sku: str):
        \"\"\"Look up a SKU's price and stock level by its code.\"\"\"
"""


def _servers(result: ScanResult) -> list[tuple[str, str | None, str | None]]:
    return [(s.id, s.transport, s.endpoint) for s in result.mcp_servers]


def test_an_undeclared_server_reports_no_transport_and_no_issue(tmp_path: Path) -> None:
    result = scan(_write(tmp_path, {"m.py": SERVERS}))

    assert _servers(result) == [("hr-tools", None, None), ("sku-manager", None, None)]
    assert result.issues == ()
    data = json.loads(result.to_json())
    assert data["mcp_servers"][0] == {
        "id": "hr-tools",
        "tools": ["find_employee"],
        "transport": None,
        "endpoint": None,
    }


def test_declared_transport_and_endpoint_are_read(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "m.py": SERVERS,
            ".donkey-kit.toml": """\
                [donkey]
                llm_proxy_url = "https://gw.example.com/ddk/"

                [donkey.mcp_servers.hr-tools]
                transport = "streamable-http"
                endpoint = "https://hr-tools.acme.com/mcp"

                [donkey.mcp_servers.sku-manager]
                transport = "stdio"
            """,
        },
    )
    result = scan(root)

    assert _servers(result) == [
        ("hr-tools", "streamable-http", "https://hr-tools.acme.com/mcp"),
        ("sku-manager", "stdio", None),
    ]
    assert result.issues == ()


def test_the_local_file_overrides_the_project_file_key_by_key(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "m.py": SERVERS,
            ".donkey-kit.toml": """\
                [donkey.mcp_servers.hr-tools]
                transport = "streamable-http"
                endpoint = "https://hr-tools.acme.com/mcp"
            """,
            ".donkey-kit.local.toml": """\
                [donkey.mcp_servers.hr-tools]
                endpoint = "http://localhost:8080/mcp"
            """,
        },
    )
    result = scan(root)

    assert _servers(result)[0] == ("hr-tools", "streamable-http", "http://localhost:8080/mcp")


def test_the_declaration_is_part_of_the_digest(tmp_path: Path) -> None:
    root = _write(tmp_path, {"m.py": SERVERS})
    before = scan(root).digest()

    (root / ".donkey-kit.toml").write_text(
        '[donkey.mcp_servers.sku-manager]\ntransport = "stdio"\n', encoding="utf-8"
    )

    assert scan(root).digest() != before


def test_a_single_file_scan_reads_the_toml_beside_it(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {"m.py": SERVERS, ".donkey-kit.toml": '[donkey.mcp_servers.hr-tools]\ntransport = "sse"\n'},
    )

    assert _servers(scan(root / "m.py"))[0] == ("hr-tools", "sse", None)


@pytest.mark.parametrize(
    ("entry", "kind", "detail"),
    [
        ('transport = "websocket"', "invalid-transport", "one of stdio, streamable-http, sse"),
        ("transport = 3", "invalid-transport", "not 3"),
        ('endpoint = "hr-tools.acme.com/mcp"', "invalid-endpoint", "http or https URL"),
        ('endpoint = "ftp://hr-tools.acme.com"', "invalid-endpoint", "http or https URL"),
        ('endpoint = "https://h:notaport/mcp"', "invalid-endpoint", "http or https URL"),
        ('endpoint = ["https://a"]', "invalid-endpoint", "http or https URL"),
        ('transport = "stdio"\nendpoint = "https://a.com"', "invalid-endpoint", "no endpoint"),
        ("port = 8080", "invalid-mcp-server", "unknown key 'port'"),
    ],
)
def test_invalid_declarations_are_issues_not_guesses(
    tmp_path: Path, entry: str, kind: str, detail: str
) -> None:
    root = _write(
        tmp_path, {"m.py": SERVERS, ".donkey-kit.toml": f"[donkey.mcp_servers.hr-tools]\n{entry}\n"}
    )
    result = scan(root)

    (issue,) = result.issues
    assert (issue.kind, issue.subject, issue.path, issue.line) == (
        kind,
        "mcp server hr-tools",
        ".donkey-kit.toml",
        0,
    )
    assert detail in issue.detail
    hr_tools = result.mcp_servers[0]
    # The valid half of an entry is kept; the invalid half is never substituted.
    assert hr_tools.transport in (None, "stdio")
    assert hr_tools.endpoint is None


def test_an_endpoint_with_credentials_is_refused_without_echoing_them(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "m.py": SERVERS,
            ".donkey-kit.toml": (
                "[donkey.mcp_servers.hr-tools]\n"
                'transport = "streamable-http"\n'
                'endpoint = "https://svc:s3cret@hr-tools.acme.com/mcp"\n'
            ),
        },
    )
    (issue,) = scan(root).issues

    assert issue.kind == "invalid-endpoint"
    assert "s3cret" not in issue.detail


def test_a_declaration_no_tool_names_is_reported(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "m.py": SERVERS,
            ".donkey-kit.local.toml": '[donkey.mcp_servers.hr-tool]\ntransport = "stdio"\n',
        },
    )
    (issue,) = scan(root).issues

    assert (issue.kind, issue.subject, issue.path) == (
        "unknown-server",
        "mcp server hr-tool",
        ".donkey-kit.local.toml",
    )


@pytest.mark.parametrize(
    ("toml", "kind", "subject"),
    [
        ("[donkey\n", "syntax-error", ".donkey-kit.toml"),
        ('[donkey]\nmcp_servers = "hr-tools"\n', "invalid-mcp-server", "donkey.mcp_servers"),
        ('[donkey.mcp_servers]\nhr-tools = "stdio"\n', "invalid-mcp-server", "mcp server hr-tools"),
        (
            '[donkey.mcp_servers.HR_Tools]\ntransport = "stdio"\n',
            "invalid-service-id",
            "mcp server HR_Tools",
        ),
    ],
)
def test_malformed_toml_is_an_issue_not_a_crash(
    tmp_path: Path, toml: str, kind: str, subject: str
) -> None:
    root = _write(tmp_path, {"m.py": SERVERS, ".donkey-kit.toml": toml})

    result = scan(root)

    assert [(i.kind, i.subject) for i in result.issues] == [(kind, subject)]
    assert _servers(result) == [("hr-tools", None, None), ("sku-manager", None, None)]


def test_the_declaration_table_does_not_disturb_donkey_config(tmp_path: Path) -> None:
    from donkey_kit.core.config import DonkeyConfig

    toml = (
        _write(
            tmp_path,
            {
                ".donkey-kit.toml": """\
                [donkey]
                llm_proxy_url = "https://gw.example.com/ddk/"

                [donkey.mcp_servers.hr-tools]
                transport = "stdio"
            """
            },
        )
        / ".donkey-kit.toml"
    )

    assert DonkeyConfig.resolve(path=toml).llm_proxy_url == "https://gw.example.com/ddk/"


def test_an_unreadable_toml_is_an_issue(tmp_path: Path) -> None:
    root = _write(tmp_path, {"m.py": SERVERS})
    (root / ".donkey-kit.toml").write_bytes(b'[donkey]\nx = "\xff"\n')

    result = scan(root)

    assert [(i.kind, i.path) for i in result.issues] == [("unreadable", ".donkey-kit.toml")]

"""``donkey scaffold`` (#1747): candidate ``.donkey-kit.toml`` tables built from
a static scan, deterministically and without importing or connecting.

Every fixture is written to ``tmp_path`` and never imported; several would
exit or have side effects if they ran.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from donkey_kit.cli import _files, app
from donkey_kit.core.toml_text import basic_string, comment, key
from donkey_kit.registry.scaffold import (
    SCAFFOLD_FORMAT,
    CandidateTable,
    Scaffold,
    ScaffoldRefused,
    build_scaffold,
)

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

runner = CliRunner()
TOML = ".donkey-kit.toml"

GOV = """\
    from donkey_kit import Donkey

    donkey = Donkey.from_env()
"""

MAIN = """\
    import scaffold_side_effect_marker  # must never be imported
    raise SystemExit("scaffold must never import this module")

    from .gov import donkey


    @donkey.tool(server="sku-manager")
    def lookup_sku(sku: str) -> dict:
        \"\"\"Look up a SKU's price and stock level by its code.\"\"\"


    @donkey.tool(server="sku-manager")
    def list_skus() -> list:
        \"\"\"List every SKU code the catalogue currently holds.\"\"\"


    @donkey.tool()
    def orphan(x: int) -> int:
        \"\"\"Return the number it is given, for a tool with no server.\"\"\"


    @donkey.agent(name="support-triage", skills=["triage"])
    async def handle(task):
        \"\"\"Triage a support ticket and draft a reply for a human to send.\"\"\"
"""


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")
    return root


def _repo(root: Path) -> Path:
    return _write(root, {"app/__init__.py": "", "app/gov.py": GOV, "app/main.py": MAIN})


def _tables(scaffold_toml: str) -> list[str]:
    return [line for line in scaffold_toml.splitlines() if line.startswith("[")]


def test_candidates_cover_every_service_and_publication(tmp_path: Path) -> None:
    result = build_scaffold(_repo(tmp_path))

    assert result.issues == ()
    assert _tables(result.to_toml()) == [
        "[donkey.agents.support-triage]",
        "[donkey.mcp_servers.sku-manager]",
        "[donkey.publication]",
        "[donkey.publication.assets.sku-manager]",
        "[donkey.publication.assets.support-triage]",
    ]
    assert result.unattributed == ("orphan",)
    assert result.declared == ()
    # Facts the code does not say are commented placeholders, never values.
    data = tomllib.loads(result.to_toml())
    assert data["donkey"]["agents"]["support-triage"] == {}
    assert data["donkey"]["mcp_servers"]["sku-manager"] == {}
    assert data["donkey"]["publication"]["assets"]["sku-manager"] == {"asset_id": "sku-manager"}
    assert '# protocol = "<a2a_v1 or other>"' in result.to_toml()
    assert "# MCP server with tools: list_skus, lookup_sku" in result.to_toml()
    assert "# agent handle, app/main.py:" in result.to_toml()
    assert result.unresolved == 6


def test_nothing_is_imported_resolved_or_contacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("scaffold must stay offline and config-free")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr("donkey_kit.core.config.DonkeyConfig.from_env", refuse)
    monkeypatch.setattr("donkey_kit.core.config.DonkeyConfig.resolve", refuse)

    result = runner.invoke(app, ["scaffold", "--dry-run", str(_repo(tmp_path))])

    assert result.exit_code == 0, result.output
    assert "scaffold_side_effect_marker" not in sys.modules
    assert "app.main" not in sys.modules


def test_the_local_overlay_user_file_and_environment_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = build_scaffold(_repo(tmp_path / "clean")).to_json()

    noisy = _repo(tmp_path / "noisy")
    (noisy / ".donkey-kit.local.toml").write_text(
        '[donkey.mcp_servers.sku-manager]\nendpoint = "https://LOCAL-SENTINEL/mcp"\n'
        '[donkey.publication]\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    home = tmp_path / "home"
    (home / ".config" / "donkey-kit").mkdir(parents=True)
    (home / ".config" / "donkey-kit" / "config.toml").write_text(
        '[donkey]\norg_id = "USER-SENTINEL"\n', encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ANYPOINT_ORG_ID", "ENV-SENTINEL")
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://ENV-SENTINEL/")

    noisy_json = build_scaffold(noisy).to_json()

    assert noisy_json == clean
    for sentinel in ("LOCAL-SENTINEL", "USER-SENTINEL", "ENV-SENTINEL", "9.9.9"):
        assert sentinel not in noisy_json


def test_output_is_byte_identical_across_checkouts(tmp_path: Path) -> None:
    first = _repo(tmp_path / "a" / "checkout")
    second = tmp_path / "elsewhere" / "deeper" / "copy"
    shutil.copytree(first, second)

    one, two = build_scaffold(first), build_scaffold(second)

    assert one.to_json() == two.to_json()
    assert one.to_json() == build_scaffold(first).to_json()
    assert str(tmp_path) not in one.to_json()


def test_declared_tables_are_left_alone_even_when_partial(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".donkey-kit.toml").write_text(
        "[donkey]\n"
        'org_id = "kept"\n'
        "[donkey.agents.support-triage]\n"  # partial: no endpoint, no protocol
        "[donkey.publication]\n"
        'version = "1.0.0"\n',
        encoding="utf-8",
    )

    result = build_scaffold(root)

    assert _tables(result.to_toml()) == [
        "[donkey.mcp_servers.sku-manager]",
        "[donkey.publication.assets.sku-manager]",
        "[donkey.publication.assets.support-triage]",
    ]
    assert result.declared == ("donkey.agents.support-triage",)


def test_a_project_key_that_is_not_a_table_is_a_conflict(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".donkey-kit.toml").write_text('[donkey]\npublication = "nope"\n', encoding="utf-8")

    result = build_scaffold(root)

    conflicts = [i for i in result.issues if i.kind == "scaffold-conflict"]
    assert [i.subject for i in conflicts] == [
        "donkey.publication",
        "donkey.publication.assets.sku-manager",
        "donkey.publication.assets.support-triage",
    ]
    assert all(i.path == ".donkey-kit.toml" and i.line == 0 for i in conflicts)
    assert "[donkey.publication]" not in result.to_toml()


@pytest.mark.parametrize(
    "content",
    [b"[donkey\n", b"\xff\xfe not utf-8"],
    ids=["invalid-toml", "invalid-utf8"],
)
def test_an_unreadable_project_file_proposes_nothing(tmp_path: Path, content: bytes) -> None:
    root = _repo(tmp_path)
    (root / ".donkey-kit.toml").write_bytes(content)

    result = build_scaffold(root)

    assert result.candidates == ()
    assert result.to_toml() == ""
    assert [i.kind for i in result.issues] in (["syntax-error"], ["unreadable"])


def test_duplicate_service_ids_get_no_table(tmp_path: Path) -> None:
    source = """\
        from donkey_kit import Donkey

        donkey = Donkey()


        @donkey.tool(server="shared")
        def lookup(code: str) -> str:
            \"\"\"Look up an item by its catalogue code.\"\"\"


        @donkey.agent(name="shared")
        def handle(task):
            \"\"\"Handle a task for the shared service identifier.\"\"\"
    """
    result = build_scaffold(_write(tmp_path, {"m.py": source}))

    assert "duplicate-service-id" in [i.kind for i in result.issues]
    assert result.candidates == ()


def test_expose_adds_gateway_tables_for_exposable_services(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".donkey-kit.toml").write_text(
        '[donkey.agents.support-triage]\nprotocol = "a2a_v1"\nendpoint = "https://t.test/"\n',
        encoding="utf-8",
    )

    result = build_scaffold(root, expose=True)

    assert _tables(result.to_toml())[-3:] == [
        "[donkey.expose]",
        "[donkey.expose.services.sku-manager]",
        "[donkey.expose.services.support-triage]",
    ]
    # Without a declared a2a_v1 protocol an agent is not exposable.
    other = build_scaffold(_repo(tmp_path / "other"), expose=True)
    assert "[donkey.expose.services.support-triage]" not in other.to_toml()
    assert "[donkey.expose.services.sku-manager]" in other.to_toml()


def test_hostile_paths_and_names_still_render_valid_toml(tmp_path: Path) -> None:
    source = """\
        from donkey_kit import Donkey

        donkey = Donkey()


        @donkey.agent(name="weird-agent")
        def handle(task):
            \"\"\"Handle a task from a file with a hostile name.\"\"\"
    """
    name = 'we"ird\\\x1b]0;x\x07\n[donkey.evil]\n.py'
    try:
        (tmp_path / name).write_text(textwrap.dedent(source), encoding="utf-8")
    except OSError:
        pytest.skip("the filesystem refuses control characters in names")

    result = build_scaffold(tmp_path)
    text = result.to_toml()

    data = tomllib.loads(text)
    assert set(data["donkey"]) == {"agents", "publication"}
    assert "\x1b" not in text and "\x07" not in text
    assert "\\u000A[donkey.evil]" in text


def test_toml_helpers() -> None:
    assert key("sku-manager") == "sku-manager"
    assert key("a.b") == '"a.b"'
    assert basic_string('q"\\\n\x00\x7f') == '"q\\"\\\\\\n\\u0000\\u007F"'
    tricky = "ü\t\x1f"
    assert tomllib.loads("x = " + basic_string(tricky))["x"] == tricky
    assert comment("a\nb\x00c\udcff\td") == "a\\u000Ab\\u0000c\\udcff\td"
    with pytest.raises(ValueError, match="surrogate"):
        basic_string("\udcff")


# --- CLI --------------------------------------------------------------------


def test_dry_run_prints_toml_on_stdout_and_a_summary_on_stderr(tmp_path: Path) -> None:
    result = runner.invoke(app, ["scaffold", "--dry-run", str(_repo(tmp_path))])

    assert result.exit_code == 0, result.output
    assert result.stdout == build_scaffold(tmp_path).to_toml()
    assert tomllib.loads(result.stdout)
    assert f"5 tables for {tmp_path / TOML}, 6 placeholders to fill in" in result.stderr
    assert "unattributed  orphan" in result.stderr


def test_json_is_one_document_matching_the_preview(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    text = runner.invoke(app, ["scaffold", "--dry-run", str(root)])
    local = runner.invoke(app, ["scaffold", "--dry-run", "--json", str(root)])
    global_ = runner.invoke(app, ["--json", "scaffold", "--dry-run", str(root)])

    assert local.exit_code == global_.exit_code == 0
    assert local.stdout == global_.stdout
    data = json.loads(local.stdout)
    assert data["scaffold_format"] == SCAFFOLD_FORMAT
    assert data["toml"] == text.stdout
    assert data["destination"] == ".donkey-kit.toml"
    assert [c["table"] for c in data["candidates"]][0] == "donkey.agents.support-triage"
    assert data["candidates"][0]["unresolved"][0] == {
        "key": "protocol",
        "hint": "a2a_v1 or other",
    }


def test_scan_issues_exit_1_with_the_document(tmp_path: Path) -> None:
    source = """\
        from donkey_kit import Donkey

        donkey = Donkey()


        @donkey.tool(server="a", approval="yes")
        def bad():
            \"\"\"A tool whose approval value the decorator rejects.\"\"\"
    """
    root = _write(tmp_path, {"m.py": source})

    result = runner.invoke(app, ["scaffold", "--dry-run", "--json", str(root)])

    assert result.exit_code == 1
    assert [i["kind"] for i in json.loads(result.stdout)["issues"]] == ["invalid-argument"]


def test_an_empty_project_has_nothing_to_add(tmp_path: Path) -> None:
    result = runner.invoke(app, ["scaffold", "--dry-run", str(tmp_path)])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert f"nothing to add to {tmp_path / TOML}" in result.stderr


def test_a_missing_path_exits_2_without_a_document(tmp_path: Path) -> None:
    result = runner.invoke(app, ["scaffold", "--dry-run", "--json", str(tmp_path / "absent")])

    assert result.exit_code == 2
    assert result.stdout == ""


# --- writing (#1748) -------------------------------------------------------


def _scaffold(root: Path, *args: str) -> Result:
    return runner.invoke(app, ["scaffold", *args, str(root)])


def _names(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir())


def test_a_bare_run_creates_the_file_the_preview_showed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    preview = _scaffold(root, "--dry-run").stdout

    result = _scaffold(root)

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert f"created {root / TOML} with 5 tables, 6 placeholders to fill in" in result.stderr
    assert (root / TOML).read_text(encoding="utf-8") == preview


def test_a_second_and_third_run_are_byte_identical_no_ops(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _scaffold(root).exit_code == 0
    first = (root / TOML).read_bytes()

    for _ in range(2):
        again = _scaffold(root, "--json")
        assert again.exit_code == 0
        data = json.loads(again.stdout)
        assert data["written"] is False
        assert data["candidates"] == []
        assert (root / TOML).read_bytes() == first


def test_missing_tables_are_appended_after_the_authored_bytes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    authored = (
        "# authored by hand: keep this comment\n"
        "[donkey]\n"
        'org_id = "o-1"   # trailing comment\n'
        "unknown_key = 3\n"
        "\n"
        '[donkey.agents."support-triage"]\n'
        'protocol = "other"\n'
        "\n"
        "[tool.other]\n"
        "x = [1, 2]\n"
    )
    (root / TOML).write_text(authored, encoding="utf-8")
    before = tomllib.loads(authored)

    result = _scaffold(root)

    assert result.exit_code == 0, result.output
    assert f"added to {root / TOML}: 4 tables" in result.stderr
    assert "already declared  donkey.agents.support-triage" in result.stderr
    text = (root / TOML).read_text(encoding="utf-8")
    assert text.startswith(authored)
    after = tomllib.loads(text)
    assert after["tool"] == before["tool"]
    assert after["donkey"]["org_id"] == "o-1"
    assert after["donkey"]["unknown_key"] == 3
    assert after["donkey"]["agents"] == {"support-triage": {"protocol": "other"}}
    assert _tables(text[len(authored) :]) == [
        "[donkey.mcp_servers.sku-manager]",
        "[donkey.publication]",
        "[donkey.publication.assets.sku-manager]",
        "[donkey.publication.assets.support-triage]",
    ]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_a_file_without_a_final_newline_keeps_its_line_endings(
    tmp_path: Path, newline: str
) -> None:
    root = _repo(tmp_path)
    authored = f'[donkey]{newline}org_id = "o-1"'
    (root / TOML).write_bytes(authored.encode())

    assert _scaffold(root).exit_code == 0

    data = (root / TOML).read_bytes()
    assert data.startswith(authored.encode() + newline.encode())
    if newline == "\n":
        assert b"\r" not in data
    else:
        assert data.count(b"\n") == data.count(b"\r\n")
    assert tomllib.loads(data.decode())["donkey"]["org_id"] == "o-1"


@pytest.mark.parametrize(
    "authored",
    [
        pytest.param("[donkey]\nagents = {}\n", id="inline-table"),
        pytest.param('donkey = { org_id = "o-1" }\n', id="inline-root"),
    ],
)
def test_a_representation_a_header_cannot_extend_is_refused_untouched(
    tmp_path: Path, authored: str
) -> None:
    root = _repo(tmp_path)
    (root / TOML).write_text(authored, encoding="utf-8")

    result = _scaffold(root, "--json")

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["written"] is False
    assert [i["kind"] for i in data["issues"]] == ["scaffold-refused"]
    assert "by hand" in data["issues"][0]["detail"]
    assert (root / TOML).read_text(encoding="utf-8") == authored
    assert _names(root) == sorted([TOML, "app"])


def test_a_table_defined_by_dotted_keys_gets_only_sub_table_headers(tmp_path: Path) -> None:
    # TOML lets a header add a sub-table to a table dotted keys defined, but not
    # re-open that table: [donkey.publication] is already declared here, so only
    # its assets are appended.
    root = _repo(tmp_path)
    authored = '[donkey]\npublication.version = "1.0.0"\n'
    (root / TOML).write_text(authored, encoding="utf-8")

    result = _scaffold(root)

    assert result.exit_code == 0, result.output
    text = (root / TOML).read_text(encoding="utf-8")
    assert text.startswith(authored)
    assert "[donkey.publication]" not in text
    publication = tomllib.loads(text)["donkey"]["publication"]
    assert publication["version"] == "1.0.0"
    assert sorted(publication["assets"]) == ["sku-manager", "support-triage"]


def test_a_dotted_key_to_an_inline_table_is_refused_untouched(tmp_path: Path) -> None:
    # The assets table exists, as an inline table no header may extend, so the
    # appended [donkey.publication.assets.<id>] would not parse.
    root = _repo(tmp_path)
    authored = "donkey.publication.assets = {}\n"
    (root / TOML).write_text(authored, encoding="utf-8")

    result = _scaffold(root)

    assert result.exit_code == 1
    assert "scaffold-refused" in result.stderr
    assert (root / TOML).read_text(encoding="utf-8") == authored


def test_scan_issues_and_conflicts_write_nothing(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    authored = '[donkey]\npublication = "not a table"\n'
    (root / TOML).write_text(authored, encoding="utf-8")

    result = _scaffold(root)

    assert result.exit_code == 1
    assert "scaffold-conflict" in result.stderr
    assert f"nothing written to {root / TOML}" in result.stderr
    assert (root / TOML).read_text(encoding="utf-8") == authored


@pytest.mark.parametrize("content", [b"[donkey\n", b"\xff\xfe"])
def test_a_malformed_project_file_is_refused_untouched(tmp_path: Path, content: bytes) -> None:
    root = _repo(tmp_path)
    (root / TOML).write_bytes(content)

    result = _scaffold(root)

    assert result.exit_code == 1
    assert (root / TOML).read_bytes() == content


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlinked_project_file_is_refused_and_its_target_untouched(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    target = tmp_path / "elsewhere.toml"
    target.write_text("[donkey]\n", encoding="utf-8")
    (root / TOML).symlink_to(target)

    result = _scaffold(root)

    assert result.exit_code == 1
    assert "symlink" in result.stderr
    assert target.read_text(encoding="utf-8") == "[donkey]\n"
    assert (root / TOML).is_symlink()


def test_the_local_overlay_is_never_read_into_the_file_nor_modified(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    overlay = root / ".donkey-kit.local.toml"
    sentinel = "-".join(["overlay", "only", "value"])
    overlay_bytes = f'[donkey]\nclient_secret = "{sentinel}"\n'.encode()
    overlay.write_bytes(overlay_bytes)

    result = _scaffold(root)

    assert result.exit_code == 0
    assert overlay.read_bytes() == overlay_bytes
    assert sentinel not in (root / TOML).read_text(encoding="utf-8")
    assert sentinel not in result.output


def test_an_edit_made_while_the_write_is_staged_is_kept_and_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    (root / TOML).write_text("[donkey]\n", encoding="utf-8")
    real_stage = _files._stage

    def stage_then_edit(path: Path, content: str) -> Path:
        staged = real_stage(path, content)
        path.write_text('[donkey]\norg_id = "edited meanwhile"\n', encoding="utf-8")
        return staged

    monkeypatch.setattr(_files, "_stage", stage_then_edit)

    result = _scaffold(root)

    assert result.exit_code == 1
    assert "changed while the write was staged" in result.stderr
    assert "edited meanwhile" in (root / TOML).read_text(encoding="utf-8")
    assert _names(root) == sorted([TOML, "app"])


def test_a_file_created_while_the_write_is_staged_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    real_stage = _files._stage

    def stage_then_create(path: Path, content: str) -> Path:
        staged = real_stage(path, content)
        path.write_text("# someone else's file\n", encoding="utf-8")
        return staged

    monkeypatch.setattr(_files, "_stage", stage_then_create)

    result = _scaffold(root)

    assert result.exit_code == 1
    assert (root / TOML).read_text(encoding="utf-8") == "# someone else's file\n"
    assert _names(root) == sorted([TOML, "app"])


def test_a_failed_replace_leaves_the_original_and_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    (root / TOML).write_text("[donkey]\n", encoding="utf-8")

    def broken(src: object, dst: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "replace", broken)

    result = _scaffold(root, "--json")

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["written"] is False
    assert "could not be written" in data["issues"][-1]["detail"]
    assert (root / TOML).read_text(encoding="utf-8") == "[donkey]\n"
    assert _names(root) == sorted([TOML, "app"])


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_an_appended_file_keeps_its_permissions(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / TOML).write_text("[donkey]\n", encoding="utf-8")
    (root / TOML).chmod(0o640)

    assert _scaffold(root).exit_code == 0

    assert (root / TOML).stat().st_mode & 0o777 == 0o640


def test_json_write_agrees_with_the_preview_and_the_file(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    preview = json.loads(_scaffold(root, "--dry-run", "--json").stdout)

    result = _scaffold(root, "--json")

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["written"] is True
    assert preview["written"] is False
    assert data["toml"] == preview["toml"] == (root / TOML).read_text(encoding="utf-8")
    assert data["candidates"] == preview["candidates"]


def test_a_single_file_root_writes_beside_it(tmp_path: Path) -> None:
    source = """\
        from donkey_kit import Donkey

        donkey = Donkey()


        @donkey.tool(server="solo")
        def ping() -> str:
            \"\"\"Answer pong, for a one-file project.\"\"\"
    """
    root = _write(tmp_path, {"pkg/solo.py": source})

    result = _scaffold(root / "pkg" / "solo.py")

    assert result.exit_code == 0, result.output
    assert "[donkey.mcp_servers.solo]" in (root / "pkg" / TOML).read_text(encoding="utf-8")
    assert not (root / TOML).exists()


def test_apply_refuses_text_that_would_change_what_it_declares() -> None:
    candidate = CandidateTable(("donkey", "agents", "a"), "a", "agent")
    plan = Scaffold(candidates=(candidate,))

    assert plan.apply(None) == plan.to_toml()
    assert Scaffold().apply("x = 1\n") == "x = 1\n"
    with pytest.raises(ScaffoldRefused, match="not valid TOML"):
        plan.apply("[donkey\n")
    with pytest.raises(ScaffoldRefused, match="by hand"):
        plan.apply("[donkey]\nagents = { b = {} }\n")


def test_replace_refuses_when_the_file_no_longer_holds_the_expected_bytes(
    tmp_path: Path,
) -> None:
    target = tmp_path / TOML
    target.write_bytes(b"new")

    with pytest.raises(_files.WriteConflict):
        _files.replace(target, "content", expected=b"old")

    assert target.read_bytes() == b"new"
    _files.replace(target, "content", expected=b"new")
    assert target.read_text(encoding="utf-8") == "content"


@pytest.mark.parametrize("flag", [["--config", "x.toml"], ["--env", "prod"]])
def test_global_connection_options_are_refused(tmp_path: Path, flag: list[str]) -> None:
    result = runner.invoke(app, [*flag, "scaffold", "--dry-run", str(tmp_path)])

    assert result.exit_code == 2

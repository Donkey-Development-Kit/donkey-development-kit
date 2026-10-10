"""``scripts/check_doc_snippets.py``: doc snippets are checked against the SDK (#797).

Every Python fence in the READMEs, ``MIGRATION.md`` and the docs site must
import, name only attributes that exist and call with arguments that bind. The
first test runs the check over the real docs, so a renamed kwarg or a moved
module fails CI here; the rest pin what the checker catches and what it
deliberately leaves alone.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_doc_snippets.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ddk_check_doc_snippets", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # the script's dataclasses look their module up
    spec.loader.exec_module(module)
    return module


check = _load()

_DOCS = check._DEFAULT_ROOT / "website" / "content"
_needs_docs = pytest.mark.skipif(
    not _DOCS.is_dir(), reason="needs the git checkout's website/, not an sdist"
)


def _page(tmp_path: Path, code: str, *, status: str | None = "live") -> Path:
    """A one-page docs tree whose only fence is ``code``."""
    page = tmp_path / "website" / "content" / "page.mdx"
    page.parent.mkdir(parents=True, exist_ok=True)
    head = f"---\ndescription: x\nstatus: {status}\n---\n\n" if status else ""
    page.write_text(f"{head}# Page\n\n```python\n{code}\n```\n", encoding="utf-8")
    return tmp_path


def _messages(root: Path) -> list[str]:
    return [problem.message for problem in check.check(root)]


@_needs_docs
def test_shipped_docs_have_no_snippet_problems() -> None:
    assert [str(problem) for problem in check.check(check._DEFAULT_ROOT)] == []


@_needs_docs
def test_shipped_docs_have_python_fences_to_check() -> None:
    # Guards the extractor: a regex that silently matched nothing would pass above.
    found = [
        snippet
        for path in check.doc_files(check._DEFAULT_ROOT)
        for snippet in check.snippets(path.read_text(encoding="utf-8"), str(path))
    ]
    assert len(found) > 100


def test_planted_bad_kwarg_on_from_env_fails(tmp_path: Path) -> None:
    root = _page(
        tmp_path,
        "from donkey_kit import Donkey\n\n"
        "async with Donkey.from_env(proxy_urll='https://x/') as donkey:\n"
        "    pass",
    )
    (message,) = _messages(root)
    assert "Donkey.from_env" in message
    assert "proxy_urll" in message


def test_bad_kwarg_found_through_an_attribute_chain(tmp_path: Path) -> None:
    root = _page(
        tmp_path,
        "from donkey_kit import Donkey\n\n"
        "async with Donkey.from_env() as donkey:\n"
        '    handle = donkey.llm.resolve("gpt-4o", provder="openai")',
    )
    (message,) = _messages(root)
    assert "LLMClient.resolve" in message
    assert "provder" in message


def test_unbound_donkey_defaults_to_a_donkey(tmp_path: Path) -> None:
    root = _page(tmp_path, "donkey.not_an_adapter.model('gpt-4o')")
    assert _messages(root) == ["`Donkey` has no attribute `not_an_adapter`"]


def test_unimported_donkey_constructors_are_still_checked(tmp_path: Path) -> None:
    # Pages import `Donkey` once and reuse it in later fences; a fence with no
    # import of its own must still be checked, and must not rebind `donkey` to
    # an unknown value that switches the checking off.
    root = _page(
        tmp_path,
        "donkey = Donkey.from_env(bad=1)\n"
        "config = DonkeyConfig.from_env(nope=2)\n"
        "donkey.not_an_adapter.model('gpt-4o')",
    )
    first, second, third = _messages(root)
    assert "Donkey.from_env" in first
    assert "'bad'" in first
    assert "DonkeyConfig.from_env" in second
    assert "'nope'" in second
    assert third == "`Donkey` has no attribute `not_an_adapter`"


def test_too_many_positional_arguments_fail(tmp_path: Path) -> None:
    root = _page(tmp_path, "from donkey_kit import DonkeyConfig\nDonkeyConfig.from_env(1, 2, 3)")
    (message,) = _messages(root)
    assert "does not match its signature" in message


def test_missing_import_name_and_module_fail(tmp_path: Path) -> None:
    root = _page(
        tmp_path,
        "from donkey_kit import NoSuchThing\nimport donkey_kit.no_such_module",
    )
    assert _messages(root) == [
        "cannot import name `NoSuchThing` from `donkey_kit`",
        "no module named `donkey_kit.no_such_module`",
    ]


@pytest.mark.skipif(sys.version_info < (3, 11), reason="3.10 skips unparseable fences")
def test_invalid_python_fails(tmp_path: Path) -> None:
    root = _page(tmp_path, "model = donkey.<framework>.<factory>()")
    (message,) = _messages(root)
    assert message.startswith("not valid Python")


def test_correct_snippet_and_unknown_objects_pass(tmp_path: Path) -> None:
    root = _page(
        tmp_path,
        "import openai\n"
        "from donkey_kit import Donkey, PIIDetected, classify\n\n"
        "async with Donkey.from_env(team='t') as donkey:\n"
        "    client = donkey.llm.client()\n"
        "    try:\n"
        "        await client.chat.completions.create(model='gpt-4o', anything=1)\n"
        "    except openai.APIStatusError as exc:\n"
        "        governed = classify(exc.response)\n"
        "        if isinstance(governed, PIIDetected):\n"
        "            print(governed.entities)",
    )
    assert _messages(root) == []


def test_roadmap_page_is_exempt(tmp_path: Path) -> None:
    root = _page(tmp_path, "donkey.approvals.pending(future_kwarg=1)", status="roadmap")
    assert _messages(root) == []


def test_nocheck_fence_is_skipped(tmp_path: Path) -> None:
    page = _page(tmp_path, "", status="live") / "website" / "content" / "page.mdx"
    page.write_text("# P\n\n```python nocheck\ndonkey.<x>\n```\n", encoding="utf-8")
    assert _messages(tmp_path) == []


def test_live_page_must_not_call_a_blocked_api(tmp_path: Path) -> None:
    code = "from donkey_kit import Donkey\nDonkey.from_env().tools.lock()"
    assert _messages(_page(tmp_path / "live", code, status="live")) == [
        "`ToolsFacade.lock` raises `_verify.blocked` (§0.3), so a `status: live` page "
        "must not call it; mark the page `status: roadmap` or drop the call"
    ]
    # Only `live` claims the call works; other statuses document it honestly.
    assert _messages(_page(tmp_path / "off", code, status="offline-verified")) == []


def test_blocked_surfaces_only_count_unconditional_raises(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "mod.py").write_text(
        "from .core import _verify\n"
        "class C:\n"
        "    def always(self):\n"
        "        '''Doc.'''\n"
        "        raise _verify.blocked('x')\n"
        "    def sometimes(self, flag):\n"
        "        if flag:\n"
        "            raise _verify.blocked('x')\n"
        "def free():\n"
        "    raise blocked('y')\n"
    )
    assert check.blocked_surfaces(package) == {"pkg.mod.C.always", "pkg.mod.free"}


def test_real_blocked_surfaces_include_the_known_guards() -> None:
    import donkey_kit

    found = check.blocked_surfaces(Path(donkey_kit.__file__).parent)
    assert "donkey_kit.donkey.ToolsFacade.lock" in found
    assert "donkey_kit.registry.publication.publish_if_changed" in found


def test_frontmatter_is_read_flat() -> None:
    text = "---\ndescription: a: b\nstatus: 'roadmap'\n---\n# x\n"
    assert check.frontmatter(text) == {"description": "a: b", "status": "roadmap"}
    assert check.frontmatter("# no frontmatter\n") == {}


def test_main_reports_and_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _page(tmp_path, "from donkey_kit import Donkey\nDonkey.from_env(nope=1)")
    assert check.main([str(root)]) == 1
    err = capsys.readouterr().err
    assert "website/content/page.mdx:" in err
    assert "1 doc snippet problem(s)." in err
    assert check.main([str(_page(tmp_path / "ok", "x = 1"))]) == 0

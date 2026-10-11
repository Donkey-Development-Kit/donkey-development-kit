"""The generated docs match the code they restate (#795).

``scripts/generate_docs.py`` writes the PyPI README, the configuration
settings index, the CLI command reference, the adapter capabilities table,
the Python API reference page and every per-framework verification status
(from the ``docs/verified-apis.md`` ledger, #1081). This test fails when a committed
copy is stale, the way the ``docs-llms-drift`` CI job fails on stale
``website/public`` files. The fix is always the same: run
``python scripts/generate_docs.py`` and commit the result.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_docs.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ddk_generate_docs", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while loading
    spec.loader.exec_module(module)
    return module


gen = _load()

pytestmark = pytest.mark.skipif(
    not (gen.REPO / "website").is_dir(),
    reason="the repository docs are not included in the sdist",
)


@pytest.mark.parametrize("target", gen.TARGETS, ids=[t.name for t in gen.TARGETS])
def test_generated_doc_is_up_to_date(target: Any) -> None:
    for module in target.needs:
        pytest.importorskip(module)
    path: Path = target.path
    assert path.is_file(), f"{path} is missing; run: python scripts/generate_docs.py"
    current = path.read_text(encoding="utf-8")
    assert current == target.expected(), (
        f"{path.relative_to(gen.REPO)} is stale; run: python scripts/generate_docs.py"
    )


def test_a_region_needs_both_markers() -> None:
    with pytest.raises(gen.GenerationError):
        gen.replace_region("no markers here\n", "x", "body")
    text = "a\n<!-- BEGIN GENERATED: x (note) -->\nold\n<!-- END GENERATED: x -->\nb\n"
    assert gen.replace_region(text, "x", "new") == (
        "a\n<!-- BEGIN GENERATED: x (note) -->\nnew\n<!-- END GENERATED: x -->\nb\n"
    )


def test_pypi_readme_links_are_absolute() -> None:
    src = (
        '<img src="brand/logo.png" />\n'
        "[License](LICENSE), [ledger](docs/verified-apis.md#4), [up](#top),\n"
        "[site](https://docs.donkey-kit.dev), [dir](docs/adr/)\n"
        "```\n[kept](relative.md)\n```\n"
    )
    out = gen.pypi_readme(src)
    blob = f"{gen.REPO_URL}/blob/main"
    assert 'src="https://raw.githubusercontent.com/' in out
    assert f"[License]({blob}/LICENSE)" in out
    assert f"[ledger]({blob}/docs/verified-apis.md#4)" in out
    assert "[up](#top)" in out
    assert "[site](https://docs.donkey-kit.dev)" in out
    assert f"[dir]({gen.REPO_URL}/tree/main/docs/adr/)" in out
    assert "[kept](relative.md)" in out


def test_docstring_markup_becomes_mdx_safe_markdown() -> None:
    text = gen.rst_to_markdown(
        "Use :class:`~donkey_kit.core.errors.ConfigError` with ``x={a}`` and "
        "a <b> tag (BG §1.1, #195), or (`BG §1.2`) here::"
    )
    assert text == "Use `ConfigError` with `x={a}` and a \\<b> tag, or here."


def test_signatures_print_string_annotations_and_mask_sentinels() -> None:
    sentinel = object()

    def f(self: object, a: int, *, b: str | None = None, c: object = sentinel) -> bool:
        return True

    assert (
        gen.format_signature("x.f", f, bound=True)
        == "x.f(a: int, *, b: str | None = None, c: object = ...) -> bool"
    )


def test_signatures_mark_coroutines_and_context_managers() -> None:
    import contextlib
    from collections.abc import AsyncIterator, Iterator

    class C:
        async def close(self) -> None: ...

        @contextlib.asynccontextmanager
        async def pace(self, *, reserve: float = 0.0) -> AsyncIterator[None]:
            yield

        @contextlib.contextmanager
        def scope(self) -> Iterator[int]:
            yield 1

    assert gen.format_signature("c.close", C.close, bound=True) == "async c.close() -> None"
    assert gen.format_signature("c.pace", C.pace, bound=True) == (
        "c.pace(*, reserve: float = 0.0) -> AbstractAsyncContextManager[None]"
    )
    assert gen.format_signature("c.scope", C.scope, bound=True) == (
        "c.scope() -> AbstractContextManager[int]"
    )


def test_api_reference_shows_async_members() -> None:
    page = gen.api_reference()
    assert "async donkey.aclose() -> None" in page
    assert "async donkey.llm.list_models(" in page
    assert "AbstractAsyncContextManager[None]" in page
    assert "AsyncIterator" not in page


def test_signatures_show_private_type_variables_without_the_underscore() -> None:
    page = gen.api_reference()
    assert "Callable[P, R]" in page
    assert "_Callable" not in page and "_P," not in page


def test_settings_index_shows_built_in_header_names() -> None:
    rows = {
        line.split("|")[1].strip(): line.split("|")[4].strip()
        for line in gen.settings_index().splitlines()
        if line.startswith("| `")
    }
    assert rows["`DONKEY_CORRELATION_HEADER`"] == "`X-Correlation-Id`"
    assert rows["`ANYPOINT_BASE_URL`"] == "from `region`"
    assert "unset" not in {rows[f"`DONKEY_COST_{d}_HEADER`"] for d in ("TEAM", "ENDUSER")}


def test_api_reference_does_not_restate_the_version() -> None:
    import donkey_kit

    # The version has two owners (pyproject.toml, __init__.py) and the site
    # banner shows it; a third copy here would go stale on every bump.
    assert f"`donkey-kit` {donkey_kit.__version__}" not in gen.api_reference()


# --- framework verification status (#1081) -------------------------------------

_LEDGER = gen.LEDGER.read_text(encoding="utf-8") if gen.LEDGER.is_file() else ""
_GEMINI_ROW = "| Google ADK (native Gemini) | `google.adk"


def _with_status(ledger: str, row_prefix: str, status: str) -> str:
    """``ledger`` with the §8 Status cell of the row starting ``row_prefix`` replaced."""
    lines = ledger.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if line.startswith(row_prefix)]
    assert len(hits) == 1, row_prefix
    cells = lines[hits[0]].split(" | ")
    cells[2] = status
    lines[hits[0]] = " | ".join(cells)
    return "".join(lines)


def test_every_adapter_factory_has_a_ledger_status() -> None:
    from donkey_kit import integrations

    rows = gen.framework_statuses()
    expected = {
        (spec.attr, name)
        for spec in integrations.ADAPTERS.values()
        for name in getattr(
            importlib.import_module(spec.module, integrations.__name__), spec.cls
        ).factories
    }
    assert {(s.attr, s.factory) for s in rows} == expected
    by_call = {s.call: s for s in rows}
    # The ledger's one VERIFIED (LIVE) framework row (§8) reads live; the rest do not.
    assert by_call["donkey.adk.gemini()"].verification == "live-verified"
    assert by_call["donkey.adk.model()"].verification == "signature-confirmed"
    assert by_call["donkey.langgraph.chat_model()"].conformance_tested


def test_ledger_status_labels_are_never_invented() -> None:
    assert gen.verification_of("VERIFIED (LIVE)") == "live-verified"
    assert gen.verification_of("VERIFIED (CLI)") == "live-verified"
    assert gen.verification_of("UNVERIFIED (signature confirmed; pending …)") == (
        "signature-confirmed"
    )
    for status in ("VERIFIED (plugin)", "VERIFIED-SHAPE-ONLY", "UNVERIFIED", "VERIFIED"):
        with pytest.raises(gen.GenerationError):
            gen.verification_of(status)


def test_flipping_a_ledger_row_flips_the_docs() -> None:
    flipped = _with_status(_LEDGER, "| Strands | `strands.models", "VERIFIED (LIVE)")
    rows = gen.framework_statuses(flipped)
    strands = next(s for s in rows if s.attr == "strands")
    assert strands.verification == "live-verified"
    assert gen.page_statuses(rows)["/frameworks/strands"] == "live"

    demoted = _with_status(
        _LEDGER, _GEMINI_ROW, "UNVERIFIED (signature confirmed; pending maintainer sign-off)"
    )
    rows = gen.framework_statuses(demoted)
    assert {s.verification for s in rows} == {"signature-confirmed"}


def test_an_unknown_ledger_status_fails_generation() -> None:
    with pytest.raises(gen.GenerationError, match="no docs label"):
        gen.framework_statuses(_with_status(_LEDGER, _GEMINI_ROW, "VERIFIED (plugin)"))


def test_the_roster_must_cover_every_factory() -> None:
    line = (
        "| Google ADK | `donkey.adk.gemini()` | Google ADK (native Gemini) | `/frameworks/adk` |\n"
    )
    assert line in _LEDGER
    with pytest.raises(gen.GenerationError, match=r"no roster row for donkey\.adk\.gemini\(\)"):
        gen.framework_statuses(_LEDGER.replace(line, ""))
    renamed = _LEDGER.replace(line, line.replace("Google ADK (native Gemini) |", "Gemini |"))
    with pytest.raises(gen.GenerationError, match="names no §8 row"):
        gen.framework_statuses(renamed)


def test_a_page_is_rated_by_its_default_factory() -> None:
    statuses = gen.page_statuses(gen.framework_statuses())
    # ADK's default factory is model(), signature-confirmed; gemini() does not lift the page.
    assert statuses["/frameworks/adk"] == "offline-verified"
    assert statuses["/frameworks"] == "offline-verified"


def test_card_badges_are_rewritten_in_place() -> None:
    text = (
        '<Card title="A" href="/a" badge="Old" badgeTone="live">\n  body\n</Card>\n'
        '<Card title="B" href="/b" badge="Keep" badgeTone="neutral">\n</Card>\n'
    )
    out = gen._card_badges(text, {"/a": ("New", "accent")}, "x")
    assert '<Card title="A" href="/a" badge="New" badgeTone="accent">' in out
    assert 'badge="Keep"' in out
    with pytest.raises(gen.GenerationError):
        gen._card_badges(text, {"/missing": ("New", "accent")}, "x")


def test_frontmatter_status_is_rewritten_in_place() -> None:
    text = "---\ndescription: d\nstatus: live\n---\n\nstatus: body text\n"
    out = gen._frontmatter_status(text, "offline-verified", "x")
    assert out == "---\ndescription: d\nstatus: offline-verified\n---\n\nstatus: body text\n"
    with pytest.raises(gen.GenerationError):
        gen._frontmatter_status("---\ndescription: d\n---\n", "live", "x")


# --- framework version compatibility (#1068) ---------------------------------------

_VERSION_TARGETS = [
    t for t in gen.TARGETS if t.name in ("framework-support", "framework-compatibility")
]
_OPEN_ROW = "| `langgraph` × `langchain-core` |"


def _copy_constraints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    copy = tmp_path / "constraints"
    copy.mkdir()
    for lock in gen.CONSTRAINTS.glob("adapter-contract-*.txt"):
        (copy / lock.name).write_text(lock.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(gen, "CONSTRAINTS", copy)
    return copy


def test_both_version_pages_are_targets() -> None:
    assert [t.path for t in _VERSION_TARGETS] == [gen.FRAMEWORK_SUPPORT, gen.COMPATIBILITY_PAGE]


def test_moving_a_pin_without_regenerating_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _copy_constraints(tmp_path, monkeypatch) / "adapter-contract-crewai-py3.12.txt"
    text = lock.read_text(encoding="utf-8")
    pinned = next(line for line in text.splitlines() if line.startswith("crewai=="))
    lock.write_text(text.replace(pinned, "crewai==99.0.0"), encoding="utf-8")
    assert gen.stale(_VERSION_TARGETS) == ["framework-support", "framework-compatibility"]
    assert "| 99.0.0 |" in gen.framework_versions_table(mdx=True)


def test_every_adapter_has_a_row_with_each_package_of_its_extra() -> None:
    from donkey_kit import integrations

    versions = gen.framework_versions()
    assert [v.extra for v in versions] == [s.extra for s in integrations.ADAPTERS.values()]
    langgraph = versions[0]
    assert [p.name for p in langgraph.packages] == [
        "langgraph",
        "langchain-openai",
        "langchain-core",
    ]
    assert all(p.minimum and p.tested for v in versions for p in v.packages)


def test_only_open_incompatibilities_are_listed_against_the_packages_they_name() -> None:
    by_extra = {v.extra: v.incompatible for v in gen.framework_versions()}
    assert [row.status for row in by_extra["langgraph"]] == ["OPEN (upstream)"]
    # The mitigated anthropic, openai and co-install rows are on record, not listed.
    assert all(not rows for extra, rows in by_extra.items() if extra != "langgraph")
    rows = gen.incompatibilities(_LEDGER)
    assert any(row.status == "MITIGATED" and "anthropic" in row.packages for row in rows)

    mitigated = _LEDGER.replace(_OPEN_ROW, _OPEN_ROW, 1)
    lines = mitigated.splitlines(keepends=True)
    hit = next(i for i, line in enumerate(lines) if line.startswith(_OPEN_ROW))
    lines[hit] = lines[hit].replace("OPEN (upstream)", "MITIGATED")
    assert not any(v.incompatible for v in gen.framework_versions("".join(lines)))


def test_an_unknown_incompatibility_status_fails_generation() -> None:
    lines = _LEDGER.splitlines(keepends=True)
    hit = next(i for i, line in enumerate(lines) if line.startswith(_OPEN_ROW))
    lines[hit] = lines[hit].replace("OPEN (upstream)", "WONTFIX")
    with pytest.raises(gen.GenerationError, match="_INCOMPATIBILITY_STATUSES"):
        gen.incompatibilities("".join(lines))


def test_a_missing_lock_or_pin_fails_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    constraints = _copy_constraints(tmp_path, monkeypatch)
    lock = constraints / "adapter-contract-adk-py3.12.txt"
    text = lock.read_text(encoding="utf-8")
    lock.write_text(
        "\n".join(line for line in text.splitlines() if not line.startswith("litellm==")),
        encoding="utf-8",
    )
    with pytest.raises(gen.GenerationError, match="pins no litellm"):
        gen.framework_versions()
    lock.unlink()
    with pytest.raises(gen.GenerationError, match="compile_constraints.py"):
        gen.framework_versions()


def test_a_requirement_without_a_plain_floor_fails_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    text = gen.PYPROJECT.read_text(encoding="utf-8")
    assert '"crewai>=1.15.3"' in text
    pyproject.write_text(text.replace('"crewai>=1.15.3"', '"crewai~=1.15"'), encoding="utf-8")
    monkeypatch.setattr(gen, "PYPROJECT", pyproject)
    with pytest.raises(gen.GenerationError, match="not a plain floor"):
        gen.framework_versions()

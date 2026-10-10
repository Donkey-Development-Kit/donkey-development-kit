"""The generated docs match the code they restate (#795).

``scripts/generate_docs.py`` writes the PyPI README, the configuration
settings index, the CLI command reference, the adapter capabilities table and
the Python API reference page from the code. This test fails when a committed
copy is stale, the way the ``docs-llms-drift`` CI job fails on stale
``website/public`` files. The fix is always the same: run
``python scripts/generate_docs.py`` and commit the result.
"""

from __future__ import annotations

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

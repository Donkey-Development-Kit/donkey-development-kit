"""``scripts/check_doc_badges.py``: a site badge claims no more than its ledger row (#1482).

The first test runs the check over the real site and ledger; the rest pin what
it catches, on a fixture page against a fixture ledger.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_doc_badges.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ddk_check_doc_badges", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # the script's dataclasses look their module up
    spec.loader.exec_module(module)
    return module


check = _load()

_needs_docs = pytest.mark.skipif(
    not check._DEFAULT_CONTENT.is_dir(), reason="needs the git checkout's website/, not an sdist"
)

_LEDGER = """\
## 2. LLM Proxy

| Item | Where used | Status | Verified value |
|---|---|---|---|
| Streaming support | `llm/client.py` | VERIFIED (LIVE) | SSE |
| Auth: plain bearer token (#836) | `core/config.py` | PARTIAL | — |

### Per-upstream route matrix

| Item | Where used | Status |
|---|---|---|
| `/responses` × OpenAI | `a \\| b` | VERIFIED (LIVE) |

## 8. Framework APIs

| Framework | Constructor | Status |
|---|---|---|
| LangGraph | `ChatOpenAI` | VERIFIED (LIVE) |
| LangGraph (tool approval, #212) | `interrupt()` | UNVERIFIED (signature confirmed; pending) |

## 9. MCP binding

| Framework | Status |
|---|---|
| LangGraph | VERIFIED (local round-trip) |
"""


def _problems(
    tmp_path: Path, page: str, allowed: dict[tuple[str, str], str] | None = None
) -> list[str]:
    name = "page.mdx"
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text(page, encoding="utf-8")
    return list(check.check(tmp_path, _LEDGER, allowed or {}))


@_needs_docs
def test_every_site_badge_matches_the_ledger() -> None:
    assert check.main([]) == 0


@pytest.mark.parametrize(
    "badge",
    [
        '<Badge tone="live" ledger="§2 Streaming support">Live</Badge>',
        '<Badge tone="live" ledger="§2 Stream">Live</Badge>',  # the start of one row
        '<Badge tone="live" ledger="§2 `/responses` × OpenAI">Live</Badge>',  # a sub-section
        '<Badge tone="neutral" ledger="§8 LangGraph (tool">Offline-verified</Badge>',
        '<Badge tone="neutral" ledger="§9 LangGraph">Local round-trip</Badge>',
        '<Badge tone="neutral" ledger="§9 LangGraph">Offline-verified</Badge>',
        '<Badge tone="live" ledger="§2 Streaming support; §8 LangGraph">Live</Badge>',
        '<Badge tone="roadmap">Roadmap</Badge>',
        '<Badge tone="neutral">Not supported</Badge>',
    ],
)
def test_a_badge_its_row_supports_passes(tmp_path: Path, badge: str) -> None:
    assert _problems(tmp_path, f"## Heading {badge}\n") == []


@pytest.mark.parametrize(
    ("badge", "message"),
    [
        (
            '<Badge tone="live" ledger="§8 LangGraph (tool approval, #212)">Live</Badge>',
            "claims live, but '§8 LangGraph (tool approval, #212)' reads 'UNVERIFIED",
        ),
        ('<Badge tone="live" ledger="§9 LangGraph">Live</Badge>', "(local round-trip)"),
        ('<Badge tone="live" ledger="§2 Auth: plain">Live</Badge>', "reads 'PARTIAL' (no claim)"),
        (
            '<Badge tone="live" ledger="§2 Streaming support; §2 Auth: plain">Live</Badge>',
            "'§2 Auth: plain' reads 'PARTIAL'",
        ),
        ('<Badge tone="live">Live</Badge>', "cites no ledger row"),
        ('<Badge tone="neutral">Offline-verified</Badge>', "cites no ledger row"),
        ('<Badge tone="live" ledger="§2 Nothing like it">Live</Badge>', "names no ledger row"),
        ('<Badge tone="live" ledger="§8 Lang">Live</Badge>', "2 ledger rows"),
        ('<Badge tone="live" ledger="Streaming support">Live</Badge>', "unreadable"),
        ('<Badge tone="live">Shiny</Badge>', "unknown badge text 'Shiny'"),
    ],
)
def test_a_badge_that_claims_more_than_its_row_fails(
    tmp_path: Path, badge: str, message: str
) -> None:
    [problem] = _problems(tmp_path, f"intro\n\n## Heading {badge}\n")
    assert problem.startswith("page.mdx:3: ")
    assert message in problem


def test_code_fences_and_generated_regions_are_skipped(tmp_path: Path) -> None:
    page = (
        '```mdx\n<Badge tone="live">Live</Badge>\n```\n'
        '{/* BEGIN GENERATED: x (run it) */}\n<Badge tone="live">Live</Badge>\n'
        "{/* END GENERATED: x */}\n"
    )
    assert _problems(tmp_path, page) == []


def test_an_allowed_badge_passes_and_a_stale_entry_fails(tmp_path: Path) -> None:
    allowed = {("page.mdx", "| Any Python code |"): "in-process", ("page.mdx", "| Gone"): "x"}
    page = '| Any Python code | x | <Badge tone="live">Live</Badge> |\n'
    problems = _problems(tmp_path, page, allowed)
    assert problems == ["page.mdx: the ALLOWED entry '| Gone' matches no badge line"]


def test_main_reports_and_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    content = tmp_path / "content"
    content.mkdir()
    (content / "page.mdx").write_text('<Badge tone="live">Live</Badge>\n', encoding="utf-8")
    ledger = tmp_path / "ledger.md"
    ledger.write_text(_LEDGER, encoding="utf-8")
    assert check.main([str(content), str(ledger)]) == 1
    err = capsys.readouterr().err
    assert "page.mdx:1: the 'Live' badge cites no ledger row" in err

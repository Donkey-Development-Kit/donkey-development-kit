"""Rendering the conformance :class:`~.suite.Result` list as a table (#191).

Dependency-free on purpose: the pytest plugin calls this from
``pytest_terminal_summary`` and a programmatic caller can render the same table
without pytest installed. No colour codes, no terminal-width probing — a plain
monospace block that reads the same in CI logs, a terminal, and a PR comment.
"""

from __future__ import annotations

from collections.abc import Sequence

from .suite import Result, Status

__all__ = ["StatusCounts", "count_statuses", "format_status", "render_report"]

# The report is the SDK "telling the team something true about their own code",
# so the glyphs are unambiguous in a plain CI log — no colour dependency.
_GLYPH: dict[Status, str] = {"pass": "PASS", "fail": "FAIL", "exempt": "EXEMPT"}


class StatusCounts(dict[Status, int]):
    """Counts per status with convenience accessors, so callers read
    ``counts.failed`` rather than indexing a bare dict."""

    @property
    def passed(self) -> int:
        """Number of scenarios that passed."""
        return self.get("pass", 0)

    @property
    def failed(self) -> int:
        """Number of scenarios that failed."""
        return self.get("fail", 0)

    @property
    def exempt(self) -> int:
        """Number of scenarios the adapter is exempt from."""
        return self.get("exempt", 0)

    @property
    def total(self) -> int:
        """Number of scenarios run, of any status."""
        return self.passed + self.failed + self.exempt


def count_statuses(results: Sequence[Result]) -> StatusCounts:
    """Count ``results`` by status."""
    counts = StatusCounts()
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def format_status(status: Status) -> str:
    """The fixed-width label for one status (``PASS``/``FAIL``/``EXEMPT``)."""
    return _GLYPH.get(status, status.upper())


def _summary_line(counts: StatusCounts) -> str:
    parts = [f"{counts.passed} passed", f"{counts.failed} failed"]
    if counts.exempt:
        parts.append(f"{counts.exempt} exempt")
    return f"Donkey conformance: {', '.join(parts)}"


def render_report(results: Sequence[Result], *, title: str = "Donkey conformance") -> str:
    """Render the results as a titled, aligned status table with a summary line.

    Each row is ``<STATUS>  <scenario title> — <detail>``. Rows are shown in the
    order given (the suite's canonical scenario order), so the table reads the
    same across runs. An empty result set renders the header and a single
    ``(no scenarios ran)`` line rather than a bare title, so a misconfigured run
    is obvious instead of silent."""
    lines = [title, "=" * len(title)]
    if not results:
        lines.append("(no scenarios ran)")
        return "\n".join(lines)

    status_width = max(len(format_status(r.status)) for r in results)
    title_width = max(len(r.title) for r in results)
    for result in results:
        label = format_status(result.status).ljust(status_width)
        name = result.title.ljust(title_width)
        detail = f" — {result.detail}" if result.detail else ""
        lines.append(f"{label}  {name}{detail}")

    lines.append("")
    lines.append(_summary_line(count_statuses(results)))
    return "\n".join(lines)

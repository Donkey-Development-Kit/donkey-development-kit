"""Find framework releases newer than the PR CI lock and plan their issues (#1066).

PR CI installs each framework from its committed lock,
``constraints/adapter-contract-<extra>-py3.12.txt`` (ADR 0007 rule 3), and the
nightly resolves everything fresh at once (rule 4). Neither says which upstream
release broke what. ``.github/workflows/framework-release-watch.yml`` runs this
script to find each watched package whose newest stable PyPI release is ahead
of its pin, test that one release against an otherwise unchanged lock, and
keep one issue per release open until the lock catches up.

The watched set is every framework extra (one per adapter-contract lock file,
which ``tests/unit/test_check_framework_releases.py`` holds equal to
``ADAPTERS``) times that extra's direct requirements in ``pyproject.toml``.
Pre-releases, dev releases and fully yanked releases are never "latest".

    python scripts/check_framework_releases.py [--json] [--package NAME]
        # releases ahead of the lock: a table, or the workflow's matrix as JSON
    python scripts/check_framework_releases.py --json --package NAME \\
        --replay LOCKED..LATEST
        # one past release, as if the lock still pinned LOCKED (#1067)
    python scripts/check_framework_releases.py --constraints-for PKG==VER \\
        --extra EXTRA --out PATH
        # the extra's lock with only PKG's pin moved to VER
    python scripts/check_framework_releases.py --record-result PATH \\
        --release JSON --mode locked|co-bump --outcome STEP=OUTCOME ...
        # one evaluate leg's result.json, from inside its venv
    python scripts/check_framework_releases.py --plan-issues --results DIR \\
        [--impacts DIR] --open-issues FILE --run-url URL --repo-url URL --out PLAN
        # the create/edit/comment/close operations for the alert job; prints
        # the rendered plan as Markdown for the job summary

Uses only the standard library (plus ``check_new_dependencies.py`` and
``review_framework_release.py`` beside it), so the detect and alert jobs run it
without installing the package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from check_new_dependencies import Fetch, canonicalize, fetch_project, requirement_name
from review_framework_release import REVIEWED, UNAVAILABLE, load_impacts, render_impact

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

_PYTHON_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYPROJECT = _PYTHON_ROOT / "pyproject.toml"
DEFAULT_CONSTRAINTS = _PYTHON_ROOT / "constraints"
_LOCK_PYTHON = "3.12"
_LOCK = re.compile(rf"^adapter-contract-(?P<extra>.+)-py{re.escape(_LOCK_PYTHON)}\.txt$")
_PIN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>\S+?)\s*(?:#.*)?$")
# PEP 440's public version grammar, case-insensitive, with its optional separators.
_VERSION = re.compile(
    r"""^v?(?:(?P<epoch>\d+)!)?(?P<release>\d+(?:\.\d+)*)
    (?P<pre>[-_.]?(?:a|b|c|rc|alpha|beta|pre|preview)[-_.]?\d*)?
    (?P<post>-\d+|[-_.]?(?:post|rev|r)[-_.]?\d*)?
    (?P<dev>[-_.]?dev[-_.]?\d*)?
    (?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?$""",
    re.IGNORECASE | re.VERBOSE,
)
_CHECKS = (
    ("install", "install"),
    ("verify", "`verify_frameworks.py --require-installed`"),
    ("pytest", "`pytest -q` (skip guard armed)"),
    ("smoke", "`smoke_example.py`"),
)
_ICONS = {"success": "✅ pass", "failure": "❌ fail", "skipped": "⏭️ skipped"}
_MARKER = re.compile(r"<!-- framework-release (?P<data>\{.*?\}) -->")
_IMPACT = re.compile(r"<!-- impact:start -->.*?<!-- impact:end -->", re.DOTALL)
_GITHUB = re.compile(r"^https?://github\.com/(?P<repo>[\w.-]+/[\w.-]+?)(?:\.git)?(?:[/#?].*)?$")


@dataclass(frozen=True, order=True)
class Version:
    """The parts of a PEP 440 version this script compares and classifies."""

    epoch: int
    release: tuple[int, ...]
    post: int  # -1 when there is no post-release segment
    prerelease: bool  # a pre- or dev release

    @classmethod
    def parse(cls, text: str) -> Version | None:
        """``text`` as a version, or ``None`` if it is not PEP 440."""
        match = _VERSION.match(text.strip())
        if match is None:
            return None
        release = tuple(int(part) for part in match["release"].split("."))
        while len(release) > 1 and release[-1] == 0:
            release = release[:-1]  # 1.2.0 == 1.2
        post = match["post"]
        return cls(
            epoch=int(match["epoch"] or 0),
            release=release,
            post=int(re.sub(r"\D", "", post) or 0) if post else -1,
            prerelease=bool(match["pre"] or match["dev"]),
        )


def bump_kind(locked: str, latest: str) -> str:
    """``major``, ``minor`` or ``patch``: the first release component that moved.

    An epoch change counts as major, and a post-release of the same version as a
    patch."""
    old, new = Version.parse(locked), Version.parse(latest)
    if old is None or new is None:
        raise ValueError(f"not a PEP 440 version: {locked!r} or {latest!r}")
    if old.epoch != new.epoch:
        return "major"
    width = max(len(old.release), len(new.release), 3)
    a = old.release + (0,) * (width - len(old.release))
    b = new.release + (0,) * (width - len(new.release))
    for index, kind in ((0, "major"), (1, "minor")):
        if a[index] != b[index]:
            return kind
    return "patch"


@dataclass(frozen=True)
class Release:
    """A project's newest stable release on PyPI."""

    version: str
    released_at: str  # date of the earliest upload, YYYY-MM-DD
    release_notes_url: str


def latest_release(project: Mapping[str, Any]) -> Release | None:
    """The newest release that is not a pre/dev release, has files, and is not
    entirely yanked. ``None`` if the project has none."""
    best: tuple[Version, str, list[Mapping[str, Any]]] | None = None
    for text, files in project.get("releases", {}).items():
        version = Version.parse(text)
        if version is None or version.prerelease:
            continue
        live = [f for f in files if not f.get("yanked")]
        if not live:
            continue
        if best is None or version > best[0]:
            best = (version, text, live)
    if best is None:
        return None
    _, text, files = best
    return _release(project, text, files)


def release_of(project: Mapping[str, Any], version: str) -> Release | None:
    """``version`` of the project if PyPI has live files for it, else ``None``."""
    files = [f for f in project.get("releases", {}).get(version, []) if not f.get("yanked")]
    return _release(project, version, files) if files else None


def _release(project: Mapping[str, Any], version: str, files: list[Mapping[str, Any]]) -> Release:
    uploaded = min(f["upload_time_iso_8601"] for f in files)
    name = project.get("info", {}).get("name", "")
    return Release(version, uploaded[:10], release_notes_url(project, name, version))


def release_notes_url(project: Mapping[str, Any], name: str, version: str) -> str:
    """The project's changelog link if it declares one, else its source or home
    page, else the version's PyPI page."""
    urls = {k.lower(): v for k, v in (project.get("info", {}).get("project_urls") or {}).items()}
    for words in (
        ("changelog", "change log", "changes", "release notes", "releases", "history"),
        ("source", "repository", "github", "code"),
        ("homepage", "home"),
    ):
        for key, url in urls.items():
            if any(word in key for word in words):
                return str(url)
    return f"https://pypi.org/project/{name}/{version}/"


def compare_url(project: Mapping[str, Any], locked: str, latest: str) -> str:
    """The GitHub compare view between the two versions' ``v`` tags when the
    project's source is on GitHub, else ``""``. Projects that tag differently
    404 here; the review prompt says so."""
    urls = {k.lower(): v for k, v in (project.get("info", {}).get("project_urls") or {}).items()}
    ranked = sorted(urls.items(), key=lambda kv: not any(w in kv[0] for w in ("source", "repo")))
    for _, url in ranked:
        match = _GITHUB.match(str(url))
        if match:
            return f"https://github.com/{match['repo']}/compare/v{locked}...v{latest}"
    return ""


def read_pins(text: str) -> dict[str, str]:
    """``{canonical name: version}`` for every ``name==version`` line of a lock."""
    pins: dict[str, str] = {}
    for line in text.splitlines():
        match = _PIN.match(line.strip())
        if match:
            pins[canonicalize(match["name"])] = match["version"]
    return pins


def rewrite_pin(text: str, package: str, version: str) -> str:
    """The lock ``text`` with exactly ``package``'s pin moved to ``version``."""
    lines = text.splitlines(keepends=True)
    hits = [
        (i, m["name"])
        for i, line in enumerate(lines)
        if (m := _PIN.match(line.strip())) and canonicalize(m["name"]) == canonicalize(package)
    ]
    if len(hits) != 1:
        raise ValueError(f"expected one pin for {package!r} in the lock, found {len(hits)}")
    index, name = hits[0]
    lines[index] = f"{name}=={version}\n"
    return "".join(lines)


@dataclass(frozen=True)
class Watch:
    """One watched package: a direct requirement of one framework extra."""

    framework: str  # verify_frameworks.py's key and the examples/ directory
    extra: str
    package: str
    locked: str


def lock_path(extra: str, constraints: Path = DEFAULT_CONSTRAINTS) -> Path:
    """The adapter-contract lock file for ``extra``."""
    return constraints / f"adapter-contract-{extra}-py{_LOCK_PYTHON}.txt"


def watched(pyproject: Mapping[str, Any], constraints: Path) -> list[Watch]:
    """Every framework extra's direct requirements, with their locked versions.

    The extras are the adapter-contract lock files', and the framework key is
    the extra with ``-`` as ``_``, as the adapter-contract nox session does."""
    extras = pyproject.get("project", {}).get("optional-dependencies", {})
    found: list[Watch] = []
    for path in sorted(constraints.glob(f"adapter-contract-*-py{_LOCK_PYTHON}.txt")):
        match = _LOCK.match(path.name)
        if match is None:
            continue
        extra = match["extra"]
        if extra not in extras:
            raise ValueError(f"{path.name}: pyproject.toml has no extra {extra!r}")
        pins = read_pins(path.read_text(encoding="utf-8"))
        for raw in extras[extra]:
            package = requirement_name(raw)
            if package not in pins:
                raise ValueError(f"{path.name}: no pin for {extra!r}'s requirement {package!r}")
            found.append(Watch(extra.replace("-", "_"), extra, package, pins[package]))
    return found


def _entry(watch: Watch, project: Mapping[str, Any], release: Release) -> dict[str, str]:
    return {
        **asdict(watch),
        "latest": release.version,
        "bump": bump_kind(watch.locked, release.version),
        "released_at": release.released_at,
        "release_notes_url": release.release_notes_url,
        "compare_url": compare_url(project, watch.locked, release.version),
    }


def _project_of(watch: Watch, fetch: Fetch) -> Mapping[str, Any]:
    project = fetch(watch.package)
    if project is None:
        raise LookupError(f"{watch.package}: not found on PyPI")
    return project


def find_releases(watches: Iterable[Watch], fetch: Fetch) -> list[dict[str, str]]:
    """One matrix entry per watched package whose newest stable release is ahead
    of its pin."""
    entries: list[dict[str, str]] = []
    for watch in watches:
        project = _project_of(watch, fetch)
        latest = latest_release(project)
        newest = Version.parse(latest.version) if latest else None
        locked = Version.parse(watch.locked)
        if latest is None or newest is None or locked is None or newest <= locked:
            continue
        entries.append(_entry(watch, project, latest))
    return entries


def replay_releases(
    watches: Iterable[Watch], fetch: Fetch, locked: str, latest: str
) -> list[dict[str, str]]:
    """The matrix entries for one past release, as if the lock pinned ``locked``.

    A manual run uses it to rerun the watch on a release with a known outcome,
    e.g. the breaks §8.1 records (#1067)."""
    old, new = Version.parse(locked), Version.parse(latest)
    if old is None or new is None or new <= old:
        raise ValueError(f"--replay needs two versions, older..newer, got {locked}..{latest}")
    entries: list[dict[str, str]] = []
    for watch in watches:
        project = _project_of(watch, fetch)
        release = release_of(project, latest)
        if release is None:
            raise LookupError(f"{watch.package} {latest}: no live release on PyPI")
        replayed = Watch(watch.framework, watch.extra, watch.package, locked)
        entries.append(_entry(replayed, project, release))
    return entries


def record_result(
    release: Mapping[str, str],
    mode: str,
    outcomes: Mapping[str, str],
    lock: Mapping[str, str],
    installed: Callable[[str], str | None],
    verify: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """One evaluate leg's result: which lock pins its venv moved, and how each
    check went. ``installed`` reads a distribution's version (``None`` if absent)."""
    package = release["package"]
    moved = {
        name: [pinned, now]
        for name, pinned in sorted(lock.items())
        if name != package and (now := installed(name)) is not None and now != pinned
    }
    failures = [
        f"{r.get('framework')}: {r.get('verdict')} {r.get('detail', '')}".strip()
        for r in verify or []
        if not str(r.get("verdict", "")).startswith(("UNVERIFIED", "VERIFIED"))
    ]
    return {
        "release": dict(release),
        "mode": mode,
        "installed": installed(package),
        "moved": moved,
        "outcomes": {step: outcomes.get(step, "skipped") for step, _ in _CHECKS},
        "verify_failures": failures,
    }


def _installed_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _digest(result: Mapping[str, Any]) -> str:
    """What makes two runs' results the same, so an unchanged rerun adds no comment."""
    keep = {k: result[k] for k in ("mode", "installed", "moved", "outcomes", "verify_failures")}
    keep["locked"] = result["release"]["locked"]
    raw = json.dumps(keep, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _green(result: Mapping[str, Any]) -> bool:
    return all(outcome == "success" for outcome in result["outcomes"].values())


def issue_title(release: Mapping[str, str]) -> str:
    """The tracking issue's title."""
    return f"framework-release: {release['package']} {release['locked']} → {release['latest']}"


def render_results(result: Mapping[str, Any], run_url: str) -> str:
    """The results section: install mode, a row per check, the run link."""
    release = result["release"]
    if result["mode"] == "locked":
        mode = f"Installed against the PR lock with only `{release['package']}` moved."
    else:
        mode = (
            f"`{release['package']}=={release['latest']}` does not resolve against the "
            "PR lock, so it was installed **unconstrained (needs co-bump)**."
        )
    lines = [mode]
    if result["moved"]:
        moved = ", ".join(f"`{n}` {a} → {b}" for n, (a, b) in result["moved"].items())
        lines.append(f"Other locked packages that moved: {moved}.")
    lines += ["", "| Check | Result |", "| --- | --- |"]
    for step, label in _CHECKS:
        outcome = result["outcomes"][step]
        lines.append(f"| {label} | {_ICONS.get(outcome, outcome)} |")
    for failure in result["verify_failures"]:
        lines.append(f"\n`verify_frameworks.py`: {failure}")
    lines.append(f"\nRun: {run_url}")
    return "\n".join(lines)


def _next_action(result: Mapping[str, Any]) -> str:
    release = result["release"]
    if not _green(result):
        return (
            "**Not safe to bump yet.** Adapt the adapter to this release, or record the "
            "incompatibility in `docs/verified-apis.md` §8.1. Never add a version "
            "ceiling (§8.4)."
        )
    co_bump = (
        " It needs the co-bumps listed above, which the recompile picks up."
        if result["mode"] != "locked"
        else ""
    )
    return (
        f"**Safe to bump.**{co_bump} The nightly lock-refresh PR (`lock-refresh.yml`) "
        "may already carry it. Otherwise, from `python/`:\n\n"
        "1. `python scripts/compile_constraints.py`\n"
        "2. `python scripts/generate_docs.py`\n"
        f"3. `python scripts/verify_frameworks.py --only {release['framework']} "
        "--emit-verified`, and record the rows in `docs/verified-apis.md` §8\n"
        "4. the pre-PR gate (CONTRIBUTING.md §1)"
    )


def render_issue(result: Mapping[str, Any], run_url: str, impact: str | None = None) -> str:
    """The whole issue body. ``impact`` is the rendered impact section (#1067),
    "impact review unavailable" when ``None``."""
    release = result["release"]
    marker = {
        "extra": release["extra"],
        "package": release["package"],
        "version": release["latest"],
        "digest": _digest(result),
    }
    lock = f"python/constraints/{lock_path(release['extra']).name}"
    return "\n".join(
        [
            f"<!-- framework-release {json.dumps(marker, sort_keys=True)} -->",
            f"`{release['package']}` {release['latest']} is out; the PR lock pins "
            f"{release['locked']}.",
            "",
            "| | |",
            "| --- | --- |",
            f"| Framework | `{release['framework']}` (extra `{release['extra']}`) |",
            f"| Locked | `{release['locked']}` in `{lock}` |",
            f"| Latest | `{release['latest']}` ({release['bump']} bump), "
            f"released {release['released_at']} |",
            f"| Release notes | {release['release_notes_url']} |",
            "",
            "## Results against the PR lock",
            "",
            render_results(result, run_url),
            "",
            "## Impact review (AI, advisory)",
            "",
            impact or UNAVAILABLE,
            "",
            "## Next action",
            "",
            _next_action(result),
            "",
            "_Opened and kept current by `framework-release-watch.yml` (docs/ci.md). "
            "It closes this issue once the lock reaches this version._",
        ]
    )


@dataclass(frozen=True)
class OpenIssue:
    """An open ``framework-release`` issue this workflow wrote."""

    number: int
    extra: str
    package: str
    version: str
    digest: str
    body: str


def parse_open_issues(raw: Iterable[Mapping[str, Any]]) -> list[OpenIssue]:
    """The open issues that carry this workflow's marker; any other is left alone."""
    found: list[OpenIssue] = []
    for issue in raw:
        body = issue.get("body") or ""
        match = _MARKER.search(body)
        if match is None:
            continue
        data = json.loads(match["data"])
        found.append(
            OpenIssue(
                int(issue["number"]),
                data["extra"],
                data["package"],
                data["version"],
                data.get("digest", ""),
                body,
            )
        )
    return found


def plan_issues(
    results: Iterable[Mapping[str, Any]],
    open_issues: Iterable[OpenIssue],
    watches: Iterable[Watch],
    run_url: str,
    lock_commit_url: Callable[[str], str],
    impacts: Mapping[tuple[str, str, str], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """The issue operations for one run, in order.

    * a release with no open issue gets one (``create``);
    * an open issue for the same release gets its body refreshed (``edit``),
      plus a ``comment`` only when the results changed. A run without an impact
      review (``impacts``, keyed by extra, package and version) keeps the
      review an earlier run wrote;
    * an open issue for an older release of a package that now has a newer one
      is closed as superseded;
    * an open issue whose release the lock has reached, or whose package is no
      longer watched, is closed with a comment linking the lock commit.
    """
    pins = {(w.extra, w.package): w.locked for w in watches}
    by_key: dict[tuple[str, str], list[OpenIssue]] = {}
    for issue in open_issues:
        by_key.setdefault((issue.extra, issue.package), []).append(issue)
    ops: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for result in sorted(results, key=lambda r: (r["release"]["extra"], r["release"]["package"])):
        release = result["release"]
        key = (release["extra"], release["package"])
        seen.add(key)
        review = (impacts or {}).get((*key, release["latest"]))
        impact = render_impact(review) if review else None
        current = [i for i in by_key.get(key, []) if i.version == release["latest"]]
        for older in (i for i in by_key.get(key, []) if i.version != release["latest"]):
            ops.append(
                {
                    "op": "close",
                    "number": older.number,
                    "reason": "not planned",
                    "comment": f"Superseded by `{release['package']}` {release['latest']}, "
                    f"tracked in its own issue: {run_url}",
                }
            )
        if not current:
            ops.append(
                {
                    "op": "create",
                    "title": issue_title(release),
                    "body": render_issue(result, run_url, impact),
                }
            )
            continue
        issue, *duplicates = current
        earlier = _IMPACT.search(issue.body)
        if impact is None and earlier and REVIEWED in earlier[0]:
            impact = earlier[0]
        ops.append(
            {
                "op": "edit",
                "number": issue.number,
                "title": issue_title(release),
                "body": render_issue(result, run_url, impact),
            }
        )
        if issue.digest != _digest(result):
            ops.append(
                {
                    "op": "comment",
                    "number": issue.number,
                    "body": "The results changed.\n\n" + render_results(result, run_url),
                }
            )
        for duplicate in duplicates:
            ops.append(
                {
                    "op": "close",
                    "number": duplicate.number,
                    "reason": "not planned",
                    "comment": f"Duplicate of #{issue.number}.",
                }
            )
    for key, issues in sorted(by_key.items()):
        if key in seen:
            continue
        extra, package = key
        for issue in issues:
            locked = pins.get(key)
            if locked is None:
                comment = f"`{package}` is no longer a direct requirement of a watched extra."
            else:
                old, new = Version.parse(issue.version), Version.parse(locked)
                if old is None or new is None or new < old:
                    continue  # still ahead of the lock; this run did not evaluate it
                comment = (
                    f"The lock now pins `{package}=={locked}` "
                    f"({lock_commit_url(extra)}), which covers {issue.version}."
                )
            ops.append(
                {"op": "close", "number": issue.number, "reason": "completed", "comment": comment}
            )
    return ops


def render_plan(ops: list[dict[str, Any]], dry_run: bool) -> str:
    """The job summary: every planned operation, with the rendered bodies."""
    lines = ["# Framework release watch", ""]
    if dry_run:
        lines += ["Dry run (pull request): no issue was written. The plan:", ""]
    if not ops:
        lines.append("Nothing to do: every watched package is at its newest release.")
    for op in ops:
        target = op.get("title") or f"#{op['number']}"
        lines.append(f"## {op['op']}: {target}")
        text = op.get("body") or op.get("comment") or ""
        lines += ["", "<details><summary>Body</summary>", "", text, "", "</details>", ""]
    return "\n".join(lines)


def _git_lock_commit_url(repo_url: str) -> Callable[[str], str]:
    def url(extra: str) -> str:
        path = lock_path(extra)
        sha = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", str(path)],
            cwd=_PYTHON_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return f"{repo_url}/commit/{sha}" if sha else f"`{path.name}`"

    return url


def _load_pyproject(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pyproject", type=Path, default=DEFAULT_PYPROJECT)
    parser.add_argument("--constraints", type=Path, default=DEFAULT_CONSTRAINTS)
    parser.add_argument("--json", action="store_true", help="print the matrix as JSON")
    parser.add_argument("--package", help="check only this watched package")
    parser.add_argument("--replay", metavar="LOCKED..LATEST", help="one past release of --package")
    parser.add_argument("--constraints-for", metavar="PKG==VER", help="write a rewritten lock")
    parser.add_argument("--extra", help="the lock --constraints-for rewrites")
    parser.add_argument("--out", type=Path, help="output file")
    parser.add_argument("--record-result", type=Path, metavar="PATH", help="write result.json")
    parser.add_argument("--release", help="the leg's matrix entry, as JSON")
    parser.add_argument("--mode", choices=("locked", "co-bump"))
    parser.add_argument("--outcome", nargs="*", default=[], metavar="STEP=OUTCOME")
    parser.add_argument("--verify-json", type=Path, help="verify_frameworks.py --json output")
    parser.add_argument("--plan-issues", action="store_true", help="plan the alert job's writes")
    parser.add_argument("--results", type=Path, help="directory of result.json files")
    parser.add_argument("--impacts", type=Path, help="directory of impact.json files")
    parser.add_argument("--open-issues", type=Path, help="gh issue list --json number,body")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--repo-url", default="")
    parser.add_argument("--dry-run", action="store_true", help="label the summary a dry run")
    return parser


def main(argv: list[str], fetch: Fetch = fetch_project) -> int:
    args = _parser().parse_args(argv)
    watches = watched(_load_pyproject(args.pyproject), args.constraints)

    if args.constraints_for:
        package, _, version = args.constraints_for.partition("==")
        if not (version and args.extra and args.out):
            print("::error::--constraints-for needs PKG==VER, --extra and --out")
            return 2
        lock = lock_path(args.extra, args.constraints)
        text = rewrite_pin(lock.read_text(encoding="utf-8"), package, version)
        args.out.write_text(text, encoding="utf-8")
        return 0

    if args.record_result:
        release = json.loads(args.release)
        outcomes = dict(item.partition("=")[::2] for item in args.outcome)
        lock = read_pins(lock_path(release["extra"], args.constraints).read_text(encoding="utf-8"))
        verify = None
        if args.verify_json and args.verify_json.is_file() and args.verify_json.stat().st_size:
            verify = json.loads(args.verify_json.read_text(encoding="utf-8"))
        result = record_result(release, args.mode, outcomes, lock, _installed_version, verify)
        args.record_result.parent.mkdir(parents=True, exist_ok=True)
        args.record_result.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 0

    if args.plan_issues:
        files = (
            sorted(args.results.rglob("*.json")) if args.results and args.results.is_dir() else []
        )
        results = [json.loads(f.read_text(encoding="utf-8")) for f in files]
        raw = json.loads(args.open_issues.read_text(encoding="utf-8")) if args.open_issues else []
        ops = plan_issues(
            results,
            parse_open_issues(raw),
            watches,
            args.run_url,
            _git_lock_commit_url(args.repo_url),
            load_impacts(args.impacts),
        )
        if args.out:
            args.out.write_text(json.dumps(ops, indent=2), encoding="utf-8")
        print(render_plan(ops, args.dry_run))
        return 0

    if args.package:
        wanted = canonicalize(args.package)
        watches = [w for w in watches if w.package == wanted]
        if not watches:
            print(f"::error::{args.package} is not a watched package")
            return 2
    if args.replay:
        locked, sep, latest = args.replay.partition("..")
        if not (args.package and sep):
            print("::error::--replay needs --package and LOCKED..LATEST")
            return 2
        entries = replay_releases(watches, fetch, locked.strip(), latest.strip())
    else:
        entries = find_releases(watches, fetch)
    if args.json:
        print(json.dumps(entries))
    elif not entries:
        print("Every watched package is at its newest stable release.")
    else:
        for e in entries:
            print(
                f"{e['package']:<32} {e['locked']:>12} -> {e['latest']:<12} "
                f"{e['bump']:<6} {e['released_at']}  ({e['extra']})"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Alert on a relative regression in the GenAI span-overhead benchmark (#753).

``tests/benchmark/test_span_overhead.py`` enforces a fixed 1 ms budget
(BG §1.6, #194). At ~18 us/call measured that is ~55x headroom, so a 10x
regression would still pass, and nothing compared one run with the next.

This script compares the current run's result with the one recorded by the most
recent earlier successful ``main`` run of the same workflow. The benchmark test
writes the result (one ``{"name", "value", "unit"}`` JSON record) to the path in
``DONKEY_BENCHMARK_JSON``; CI uploads it as the ``--artifact-name`` artifact on
``main`` pushes, and this script downloads earlier runs' copies through the
GitHub REST API, which needs the ``actions: read`` permission. It needs no
gh-pages branch and no third-party action.

    python scripts/compare_benchmark_trend.py --current CURRENT.json \\
        --repo OWNER/REPO --workflow ci.yml --run-id RUN_ID \\
        [--artifact-name benchmark-result] [--alert-threshold 2.0] [--token TOKEN]

Exits 1 with a ``::error::`` annotation when ``current / previous`` is at or
above ``--alert-threshold`` (default 2.0). Exits 0 with a ``::notice::`` when
the ratio is below it, or when there is no comparable earlier result yet (the
first recorded ``main`` run is the baseline). A failure to reach the API exits 0
with a ``::warning::``, so a GitHub outage does not turn ``main`` red. Uses only
the standard library, so CI can run it without installing the package.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from typing import Any

_API = "https://api.github.com"

PreviousFetcher = Callable[[str, str, str, str, str], "dict[str, Any] | None"]


def _open(url: str, token: str) -> bytes:
    """GET ``url`` with the token, which is NOT forwarded on a redirect.

    The artifact download endpoint answers with a redirect to a signed
    blob-storage URL. ``urllib`` copies ordinary headers onto the redirected
    request, which would send the GitHub token to that third-party host (and
    a signed URL rejects a second credential), so the token goes in an
    unredirected header.
    """
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "donkey-kit-benchmark-trend",
        },
    )
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        content: bytes = response.read()
        return content


def _get_json(url: str, token: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(_open(url, token))
    return result


def previous_result(
    repo: str, workflow: str, exclude_run_id: str, artifact_name: str, token: str
) -> dict[str, Any] | None:
    """Return the ``artifact_name`` record from the most recent earlier successful
    ``main`` run of ``workflow``, or ``None`` if none of the last ten has one."""
    runs = _get_json(
        f"{_API}/repos/{repo}/actions/workflows/{workflow}/runs"
        "?branch=main&event=push&status=success&per_page=10",
        token,
    )["workflow_runs"]
    for run in runs:
        run_id = str(run["id"])
        if run_id == exclude_run_id:
            continue
        artifacts = _get_json(f"{_API}/repos/{repo}/actions/runs/{run_id}/artifacts", token)[
            "artifacts"
        ]
        matches = [a for a in artifacts if a["name"] == artifact_name and not a["expired"]]
        if not matches:
            continue
        with zipfile.ZipFile(io.BytesIO(_open(matches[0]["archive_download_url"], token))) as zf:
            names = [n for n in zf.namelist() if n.endswith(".json")]
            if not names:
                continue
            result: dict[str, Any] = json.loads(zf.read(names[0]))
            return result
    return None


def compare(
    current: Mapping[str, Any], previous: Mapping[str, Any] | None, threshold: float
) -> tuple[int, str]:
    """Return ``(exit_code, annotation)`` for ``current`` against ``previous``."""
    name, unit, value = current["name"], current["unit"], float(current["value"])
    if previous is None:
        return 0, f"::notice::no earlier main result; baseline {name}={value:.2f} {unit}"
    if previous.get("name") != name or previous.get("unit") != unit:
        return 0, (
            f"::notice::earlier result is {previous.get('name')!r} in {previous.get('unit')!r}, "
            f"not {name!r} in {unit!r}; {name}={value:.2f} {unit} is the new baseline"
        )
    previous_value = float(previous["value"])
    if previous_value <= 0:
        return 0, f"::warning::earlier {name}={previous_value} is not comparable; skipping"
    ratio = value / previous_value
    message = (
        f"{name}: {previous_value:.2f} -> {value:.2f} {unit} "
        f"({ratio:.2f}x), alert threshold {threshold:.2f}x"
    )
    if ratio >= threshold:
        return 1, f"::error::benchmark regression at or beyond threshold: {message}"
    return 0, f"::notice::benchmark within threshold: {message}"


def main(argv: list[str], fetch_previous: PreviousFetcher = previous_result) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--current", required=True, help="path to the current run's JSON result")
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--workflow", required=True, help="workflow file name, e.g. ci.yml")
    parser.add_argument("--run-id", required=True, help="current run id, excluded from the search")
    parser.add_argument("--artifact-name", default="benchmark-result")
    parser.add_argument("--alert-threshold", type=float, default=2.0)
    parser.add_argument("--token", default=None, help="defaults to $GH_TOKEN / $GITHUB_TOKEN")
    args = parser.parse_args(argv)

    token = args.token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("::warning::no GitHub token available; skipping benchmark trend comparison")
        return 0

    with open(args.current, encoding="utf-8") as fh:
        current: dict[str, Any] = json.load(fh)

    try:
        previous = fetch_previous(args.repo, args.workflow, args.run_id, args.artifact_name, token)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        # OSError covers urllib's URLError/HTTPError and socket timeouts;
        # ValueError covers a malformed JSON body.
        print(f"::warning::could not fetch the earlier benchmark result ({exc!r}); skipping")
        return 0

    code, annotation = compare(current, previous, args.alert_threshold)
    print(annotation)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

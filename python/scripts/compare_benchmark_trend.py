"""Alert on a relative regression in the GenAI span-overhead benchmark (#753).

``tests/benchmark/test_span_overhead.py`` only enforces a fixed 1 ms budget
(BG §1.6, #194) — at ~18 us/call measured, that is ~55x headroom, so a 10x
regression would still pass. Nothing trended the result across runs.

This script is the "least new infra" option from #753: no gh-pages branch, no
new third-party action. It compares the current run's JSON result (written by
the benchmark test via ``DONKEY_BENCHMARK_JSON`` when that env var is set, one
``{"name", "value", "unit"}`` record) against the previous successful
``main``-branch run's own recorded result, fetched via the GitHub REST API
using the ``actions: read`` permission (an ``actions/upload-artifact`` upload,
named ``--artifact-name``, is this script's only expectation of the caller).

    python scripts/compare_benchmark_trend.py --current CURRENT.json \\
        --repo OWNER/REPO --workflow ci.yml --run-id RUN_ID \\
        [--artifact-name benchmark-result] [--alert-threshold 2.0] [--token TOKEN]

Exits 0 and prints a ``::notice::`` when no previous result exists yet (the
first ``main`` run establishes the baseline) or the regression is within
``--alert-threshold`` (current / previous, default 2.0x). Exits 1 with a
``::error::`` on a regression at or beyond it. Uses only the standard library,
so CI can run it without installing the package.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from typing import Any

_API = "https://api.github.com"


def _get_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "donkey-kit-benchmark-trend",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result: dict[str, Any] = json.loads(response.read())
        return result


def _get_bytes(url: str, token: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "donkey-kit-benchmark-trend",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content: bytes = response.read()
        return content


def previous_result(
    repo: str, workflow: str, exclude_run_id: str, artifact_name: str, token: str
) -> dict[str, Any] | None:
    """The ``artifact_name`` JSON record from the most recent prior successful
    run of ``workflow`` on ``main``, or ``None`` if there isn't one yet."""
    runs = _get_json(
        f"{_API}/repos/{repo}/actions/workflows/{workflow}/runs"
        "?branch=main&status=success&per_page=10",
        token,
    )["workflow_runs"]
    candidates = [str(run["id"]) for run in runs if str(run["id"]) != exclude_run_id]
    for run_id in candidates:
        artifacts = _get_json(f"{_API}/repos/{repo}/actions/runs/{run_id}/artifacts", token)[
            "artifacts"
        ]
        matches = [a for a in artifacts if a["name"] == artifact_name and not a["expired"]]
        if not matches:
            continue
        archive = _get_bytes(matches[0]["archive_download_url"], token)
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            names = [n for n in zf.namelist() if n.endswith(".json")]
            if not names:
                continue
            result: dict[str, Any] = json.loads(zf.read(names[0]))
            return result
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--current", required=True, help="path to the current run's JSON result")
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--workflow", required=True, help="workflow file name, e.g. ci.yml")
    parser.add_argument("--run-id", required=True, help="current run id, excluded from the search")
    parser.add_argument("--artifact-name", default="benchmark-result")
    parser.add_argument("--alert-threshold", type=float, default=2.0)
    parser.add_argument("--token", default=None, help="defaults to $GH_TOKEN / $GITHUB_TOKEN")
    args = parser.parse_args(argv)

    token = args.token
    if token is None:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("::warning::no GitHub token available; skipping benchmark trend comparison")
        return 0

    with open(args.current, encoding="utf-8") as fh:
        current: dict[str, Any] = json.load(fh)
    current_value = float(current["value"])

    try:
        previous = previous_result(
            args.repo, args.workflow, args.run_id, args.artifact_name, token
        )
    except (urllib.error.URLError, urllib.error.HTTPError, zipfile.BadZipFile, KeyError) as exc:
        print(f"::warning::could not fetch the previous benchmark result ({exc}); skipping")
        return 0

    if previous is None:
        print(
            f"::notice::no previous '{args.workflow}' main-branch benchmark found — "
            f"recording {current['name']}={current_value:.2f} {current['unit']} as the baseline"
        )
        return 0

    previous_value = float(previous["value"])
    ratio = current_value / previous_value if previous_value else float("inf")
    message = (
        f"{current['name']}: {previous_value:.2f} -> {current_value:.2f} {current['unit']} "
        f"({ratio:.2f}x), alert threshold {args.alert_threshold:.2f}x"
    )
    if ratio >= args.alert_threshold:
        print(f"::error::benchmark regression beyond threshold: {message}")
        return 1
    print(f"::notice::benchmark within threshold: {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

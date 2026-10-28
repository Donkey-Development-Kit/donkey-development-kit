"""Run ``donkey publish`` for the scan-and-publish action (#219).

action.yml runs this with the Python of the virtual environment it installed
donkey-kit into. It reads the action's inputs from ``INPUT_*`` variables, sets
the ``ANYPOINT_*`` credentials from the ones that are not empty (so a job that
sets them in its own ``env:`` still works), and runs
``donkey --json publish``. Then it writes:

* a table of the assets to the job summary (``GITHUB_STEP_SUMMARY``);
* the ``published``, ``unchanged`` and ``skipped`` counts (``skipped`` is always
  0 now that agents are published, #1260), and the path of the JSON result, to
  the step's outputs (``GITHUB_OUTPUT``);
* each error line donkey printed as an ``::error::`` annotation when it fails,
  or each issue in the JSON when it printed none.

It exits with donkey's exit code. Standard library only: it runs before
anything but donkey-kit is installed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Input → the variable donkey-kit reads it from (docs: reference/configuration).
CREDENTIALS = {
    "INPUT_CLIENT_ID": "ANYPOINT_CLIENT_ID",
    "INPUT_CLIENT_SECRET": "ANYPOINT_CLIENT_SECRET",
    "INPUT_ORG_ID": "ANYPOINT_ORG_ID",
}
STATUSES = ("published", "unchanged", "skipped")


def main() -> int:
    """Run donkey publish with the action's inputs; return its exit code."""
    env = os.environ
    try:
        dry_run = _flag(env, "INPUT_DRY_RUN", default=False)
        if_changed = _flag(env, "INPUT_IF_CHANGED", default=True)
    except ValueError as exc:
        print(f"::error title=donkey publish::{escape(str(exc))}", flush=True)
        return 2
    command = [
        str(_donkey()),
        "--json",
        "publish",
        env.get("INPUT_PATH") or ".",
        *(["--dry-run"] if dry_run else []),
        *([] if if_changed else ["--always"]),
    ]
    done = subprocess.run(command, env=child_env(env), capture_output=True, text=True)
    sys.stderr.write(done.stderr)
    result = Path(env.get("RUNNER_TEMP") or ".") / "donkey-publish.json"
    result.write_text(done.stdout, encoding="utf-8")
    data = _parse(done.stdout)
    if data is not None:
        _append(env.get("GITHUB_STEP_SUMMARY"), summary(data, dry_run=dry_run))
        _append(env.get("GITHUB_OUTPUT"), outputs(data, result))
    if done.returncode != 0:
        for line in failures(done.stderr, data, done.returncode):
            print(f"::error title=donkey publish::{escape(line)}", flush=True)
    return done.returncode


def failures(stderr: str, data: Mapping[str, Any] | None, code: int) -> list[str]:
    """The annotation lines for a failed run: donkey's stderr, else each issue
    in the JSON (a donkey-kit before #1268 printed a dry run's issues only
    there), else the exit code."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines and data is not None:
        lines = [
            f"{i['path']}:{i['line']} {i['kind']}: {i['subject']}: {i['detail']}"
            for i in data.get("issues") or []
        ]
    return lines or [f"donkey publish exited {code}"]


def child_env(env: Mapping[str, str]) -> dict[str, str]:
    """The environment donkey runs in: the job's, plus each credential input
    that is not empty."""
    child = dict(env)
    for name, variable in CREDENTIALS.items():
        if env.get(name):
            child[variable] = env[name]
    return child


def summary(data: Mapping[str, Any], *, dry_run: bool) -> str:
    """The job summary: one row per asset, then the issues."""
    lines = [
        f"### donkey publish{' (dry run: nothing was sent)' if dry_run else ''}",
        "",
    ]
    assets = data.get("assets") or []
    if assets:
        if dry_run:
            lines += ["| Type | Asset | Digest |", "| --- | --- | --- |"]
            lines += [f"| {a['type']} | `{_gav(a)}` | `{a['digest']}` |" for a in assets]
        else:
            lines += ["| Status | Type | Asset | Detail |", "| --- | --- | --- | --- |"]
            lines += [
                f"| {a.get('status', '')} | {a['type']} | `{_gav(a)}` | {_cell(a.get('detail'))} |"
                for a in assets
            ]
    else:
        lines.append("No MCP server or agent was found.")
    issues = data.get("issues") or []
    if issues:
        lines += [
            "",
            f"**{len(issues)} issue{'' if len(issues) == 1 else 's'}**, nothing was published:",
            "",
        ]
        lines += [
            f"- `{i['path']}:{i['line']}` {i['kind']}: {i['subject']}: {_cell(i['detail'])}"
            for i in issues
        ]
    return "\n".join(lines) + "\n"


def outputs(data: Mapping[str, Any], result: Path) -> str:
    """The step outputs: a count per status, and where the JSON is."""
    assets = data.get("assets") or []
    counts = {s: sum(a.get("status") == s for a in assets) for s in STATUSES}
    return "".join(f"{s}={n}\n" for s, n in counts.items()) + f"result={result}\n"


def escape(message: str) -> str:
    """A workflow-command message: ``%``, CR and LF are escaped."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _donkey() -> Path:
    """The ``donkey`` script beside this interpreter, in the action's venv."""
    scripts = Path(sys.executable).parent
    return scripts / ("donkey.exe" if os.name == "nt" else "donkey")


def _flag(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    value = env.get(name, "").strip().lower()
    if not value:
        return default
    if value not in ("true", "false"):
        raise ValueError(
            f'{name.removeprefix("INPUT_").lower().replace("_", "-")} must be "true" or "false"'
        )
    return value == "true"


def _parse(stdout: str) -> dict[str, Any] | None:
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _gav(asset: Mapping[str, Any]) -> str:
    group = asset.get("group_id") or "<group_id>"
    return f"{group}:{asset['asset_id']}:{asset.get('version') or '<version>'}"


def _cell(text: object) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def _append(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    sys.exit(main())

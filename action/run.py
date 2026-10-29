"""Run ``donkey publish`` for the scan-and-publish action (#219).

action.yml runs this with the Python of the virtual environment it installed
donkey-kit into. It reads the action's inputs from ``INPUT_*`` variables, sets
the ``ANYPOINT_*`` credentials from the ones that are not empty (so a job that
sets them in its own ``env:`` still works), and runs
``donkey --json publish``. Then it writes:

* a table of the assets to the job summary (``GITHUB_STEP_SUMMARY``), and,
  when the publish did not finish, how many assets failed, are incomplete or
  were not attempted;
* the ``published``, ``unchanged``, ``incomplete``, ``failed`` and
  ``not_attempted`` counts, and the path of the JSON result, to the step's
  outputs (``GITHUB_OUTPUT``);
* each error line donkey printed as an ``::error::`` annotation when it fails,
  or each issue in the JSON when it printed none.

What donkey prints comes from the repository it scans, so none of it reaches
the log or the summary as markup (#1442): its stderr is echoed with workflow
commands stopped, under a token made for the run, and every field in the
summary is escaped.

donkey prints the JSON when a publish fails too (#1426), so the summary and
the outputs say what was published before the failure. The JSON must be the
document this action reads, ``plan_format`` 1 for a dry run and
``publish_format`` 1 otherwise (#1427): any other is not read, and the step
fails with an annotation naming the format it found.

It exits with donkey's exit code, or 1 when donkey exited 0 with a document
this action does not read. Standard library only: it runs before anything but
donkey-kit is installed.
"""

from __future__ import annotations

import json
import os
import re
import secrets
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
# The statuses donkey publish --json reports: PublishStatus in donkey-kit.
STATUSES = ("published", "unchanged", "incomplete", "failed", "not_attempted")
# The statuses of a publish that did not finish.
UNFINISHED = ("incomplete", "failed", "not_attempted")
# The document donkey publish --json prints, as (format key, version), for a dry
# run and for a publish: PLAN_FORMAT and PUBLISH_FORMAT in donkey-kit.
FORMATS = {True: ("plan_format", 1), False: ("publish_format", 1)}
# Markdown's inline syntax characters, and the backslash that escapes them.
_MARKUP = re.compile(r"([\\`*_~\[\]<>|])")


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
    # stdout, not stderr: the runner reads the two pipes separately, and the
    # resume line must reach it before the annotations printed below.
    print(echo(done.stderr, secrets.token_hex(16)), end="", flush=True)
    result = Path(env.get("RUNNER_TEMP") or ".") / "donkey-publish.json"
    result.write_text(done.stdout, encoding="utf-8")
    data = _parse(done.stdout)
    code = done.returncode
    problem = None if data is None else unread(data, dry_run=dry_run)
    if problem is not None:
        print(f"::error title=donkey publish::{escape(problem)}", flush=True)
        data, code = None, code or 1
    if data is not None:
        _append(env.get("GITHUB_STEP_SUMMARY"), summary(data, dry_run=dry_run))
        _append(env.get("GITHUB_OUTPUT"), outputs(data, result))
    if done.returncode != 0:
        for line in failures(done.stderr, data, done.returncode):
            print(f"::error title=donkey publish::{escape(line)}", flush=True)
    return code


def unread(data: Mapping[str, Any], *, dry_run: bool) -> str | None:
    """Why this action does not read ``data``, or ``None`` when it does: its
    format key is not the one in ``FORMATS``."""
    key, version = FORMATS[dry_run]
    if data.get(key) == version:
        return None
    found = [f"{k} {data[k]}" for k in sorted(data) if k.endswith("_format")]
    return (
        f"donkey printed a JSON document with {', '.join(found) or 'no format key'}, "
        f"and this action reads {key} {version}; use the donkey-kit of the action's "
        "own release (leave the donkey-kit input empty)"
    )


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
    issues = data.get("issues") or []
    if assets:
        if dry_run:
            lines += ["| Type | Asset | Digest |", "| --- | --- | --- |"]
            lines += [
                f"| {_cell(a['type'])} | {_code(_gav(a))} | {_code(a['digest'])} |" for a in assets
            ]
        else:
            lines += ["| Status | Type | Asset | Detail |", "| --- | --- | --- | --- |"]
            lines += [
                f"| {_cell(a.get('status'))} | {_cell(a['type'])} | {_code(_gav(a))} "
                f"| {_cell(a.get('detail'))} |"
                for a in assets
            ]
            counts = [(s, sum(a.get("status") == s for a in assets)) for s in UNFINISHED]
            # An issue stops the publish before it starts; the issues say why.
            if any(n for _, n in counts) and not issues:
                told = ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in counts if n)
                lines += [
                    "",
                    (
                        f"**The publish did not finish** ({told}). What was published stays "
                        "published; fix the error and re-run, and the rerun finishes where "
                        "this one stopped."
                    ),
                ]
    else:
        lines.append("No MCP server or agent was found.")
    if issues:
        lines += [
            "",
            f"**{len(issues)} issue{'' if len(issues) == 1 else 's'}**, nothing was published:",
            "",
        ]
        lines += [
            f"- {_code(str(i['path']) + ':' + str(i['line']), table=False)} {_cell(i['kind'])}: "
            f"{_cell(i['subject'])}: {_cell(i['detail'])}"
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


def echo(text: str, token: str) -> str:
    """``text`` for the log with workflow commands stopped: a line in it that
    looks like ``::set-output::`` or ``::add-mask::`` is printed, not run.
    Only ``::<token>::`` resumes them, so ``token`` must be one ``text`` cannot
    know: a fresh random one per run."""
    if not text:
        return ""
    end = "" if text.endswith("\n") else "\n"
    return f"::stop-commands::{token}\n{text}{end}::{token}::\n"


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
    """Plain summary text on one line: the characters markdown reads as
    links, HTML, code, emphasis or a table's column break are backslash-escaped."""
    return _MARKUP.sub(r"\\\1", _one_line(text))


def _code(text: object, *, table: bool = True) -> str:
    """``text`` as an inline code span on one line. Backslashes do not escape
    inside one, so the fence is a backtick run longer than any in ``text``. In a
    table, ``|`` is escaped all the same: GFM splits the row before it reads
    code spans."""
    value = _one_line(text)
    if table:
        value = value.replace("|", "\\|")
    fence = "`" * (max(map(len, re.findall("`+", value)), default=0) + 1)
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def _one_line(text: object) -> str:
    return re.sub(r"[\r\n]+", " ", str(text or ""))


def _append(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    sys.exit(main())

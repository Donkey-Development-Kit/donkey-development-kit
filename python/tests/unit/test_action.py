"""The scan-and-publish action's runner, ``action/run.py`` (#219).

``run.py`` is loaded from the repository root, as action.yml runs it. These
tests replace the ``donkey`` subprocess with canned output; ci.yml's
``action-self-test`` job runs the action itself against ``action/tests/fixture``.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any, get_args

import pytest
import yaml

ACTION = Path(__file__).resolve().parents[3] / "action"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("action_run", ACTION / "run.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = _load()

ASSET = {
    "asset_id": "sku-manager",
    "digest": "sha256:" + "1" * 64,
    "group_id": "org-1",
    "type": "mcp",
    "version": "1.0.0",
}


@pytest.fixture
def github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """The files a runner gives a step, and a clean set of inputs."""
    files = {"summary": tmp_path / "summary.md", "output": tmp_path / "output.txt"}
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(files["summary"]))
    monkeypatch.setenv("GITHUB_OUTPUT", str(files["output"]))
    for name in (*run.CREDENTIALS, *run.CREDENTIALS.values(), "INPUT_DRY_RUN", "INPUT_IF_CHANGED"):
        monkeypatch.delenv(name, raising=False)
    return files


def _donkey(
    monkeypatch: pytest.MonkeyPatch, stdout: str, *, stderr: str = "", code: int = 0
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(command: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kw})
        return subprocess.CompletedProcess(command, code, stdout, stderr)

    monkeypatch.setattr(run.subprocess, "run", fake)
    return calls


def test_a_publish_writes_the_summary_and_the_outputs(
    github: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INPUT_PATH", "services")
    monkeypatch.setenv("INPUT_CLIENT_ID", "cid")
    monkeypatch.setenv("INPUT_CLIENT_SECRET", "s3cret")
    body = {
        "publish_format": 1,
        "assets": [
            {**ASSET, "status": "published", "detail": "published 1.0.0"},
            {**ASSET, "asset_id": "pricing", "type": "agent", "status": "unchanged", "detail": "x"},
        ],
        "issues": [],
    }
    calls = _donkey(monkeypatch, json.dumps(body))

    assert run.main() == 0

    (call,) = calls
    assert call["command"][1:] == ["--json", "publish", "services"]
    assert call["env"]["ANYPOINT_CLIENT_ID"] == "cid"
    assert call["env"]["ANYPOINT_CLIENT_SECRET"] == "s3cret"
    assert "ANYPOINT_ORG_ID" not in call["env"]
    summary = github["summary"].read_text()
    assert "| published | mcp | `org-1:sku-manager:1.0.0` | published 1.0.0 |" in summary
    *counts, result = github["output"].read_text().splitlines()
    assert counts == [
        "published=1",
        "unchanged=1",
        "incomplete=0",
        "failed=0",
        "not_attempted=0",
    ]
    assert json.loads(Path(result.removeprefix("result=")).read_text()) == body
    assert "did not finish" not in summary


def test_a_failed_publish_still_writes_the_summary_and_the_outputs(
    github: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#1426: donkey prints the outcome JSON when an asset fails, and exits 1."""
    body = {
        "publish_format": 1,
        "assets": [
            {**ASSET, "asset_id": "pricing", "status": "published", "detail": "published 1.0.0"},
            {**ASSET, "status": "failed", "detail": "Exchange refused | publishing. Fix it."},
            {**ASSET, "asset_id": "hr", "status": "not_attempted", "detail": "not attempted"},
        ],
        "issues": [],
    }
    stderr = "Exchange refused publishing\nFix it.\n"
    _donkey(monkeypatch, json.dumps(body), stderr=stderr, code=1)

    assert run.main() == 1

    summary = github["summary"].read_text()
    assert (
        "| failed | mcp | `org-1:sku-manager:1.0.0` | Exchange refused \\| publishing." in summary
    )
    assert "**The publish did not finish** (1 failed, 1 not attempted)." in summary
    counts = github["output"].read_text().splitlines()[:5]
    assert counts == ["published=1", "unchanged=0", "incomplete=0", "failed=1", "not_attempted=1"]
    assert capsys.readouterr().out.splitlines()[-2:] == [
        "::error title=donkey publish::Exchange refused publishing",
        "::error title=donkey publish::Fix it.",
    ]


def test_an_empty_input_keeps_the_jobs_own_credential(
    github: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANYPOINT_ORG_ID", "from-job-env")
    monkeypatch.setenv("INPUT_ORG_ID", "")

    assert run.child_env(dict(run.os.environ))["ANYPOINT_ORG_ID"] == "from-job-env"


@pytest.mark.parametrize(
    ("inputs", "flags"),
    [
        ({"INPUT_DRY_RUN": "true"}, ["--dry-run"]),
        ({"INPUT_IF_CHANGED": "false"}, ["--always"]),
        ({"INPUT_DRY_RUN": "False", "INPUT_IF_CHANGED": " TRUE "}, []),
    ],
)
def test_the_flags(
    github: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    inputs: dict[str, str],
    flags: list[str],
) -> None:
    for name, value in inputs.items():
        monkeypatch.setenv(name, value)
    document = {"plan_format": 1, "publish_format": 1, "assets": [], "issues": []}
    calls = _donkey(monkeypatch, json.dumps(document))

    assert run.main() == 0

    assert calls[0]["command"][4:] == flags


def test_a_flag_that_is_not_a_boolean_exits_2(
    github: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("INPUT_DRY_RUN", "yes")
    calls = _donkey(monkeypatch, "")

    assert run.main() == 2

    assert calls == []
    assert '::error title=donkey publish::dry-run must be "true" or "false"' in (
        capsys.readouterr().out
    )


def test_the_dry_run_summary_lists_the_digests(github: dict[str, Path]) -> None:
    text = run.summary({"assets": [ASSET], "issues": []}, dry_run=True)

    assert text.startswith("### donkey publish (dry run: nothing was sent)\n")
    assert f"| mcp | `org-1:sku-manager:1.0.0` | `{ASSET['digest']}` |" in text


def test_a_failure_annotates_each_error_line_and_keeps_the_exit_code(
    github: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stopped = {**ASSET, "version": None, "status": "not_attempted", "detail": "not attempted"}
    document = {
        "publish_format": 1,
        "assets": [stopped],
        "issues": [
            {
                "kind": "missing-version",
                "subject": "donkey.publication",
                "path": ".donkey-kit.toml",
                "line": 0,
                "detail": "set version | in [donkey.publication]",
            }
        ],
    }
    stderr = "1 issue\n  .donkey-kit.toml:0  missing-version  100% wrong\nnothing was published\n"
    _donkey(monkeypatch, json.dumps(document), stderr=stderr, code=1)
    monkeypatch.setattr(run.secrets, "token_hex", lambda n: "t" * 2 * n)

    assert run.main() == 1

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines() == [
        f"::stop-commands::{'t' * 32}",
        *stderr.splitlines(),
        f"::{'t' * 32}::",
        "::error title=donkey publish::1 issue",
        "::error title=donkey publish::.donkey-kit.toml:0  missing-version  100%25 wrong",
        "::error title=donkey publish::nothing was published",
    ]
    summary = github["summary"].read_text()
    assert "| not\\_attempted | mcp | `org-1:sku-manager:<version>` | not attempted |" in summary
    assert (
        "- `.donkey-kit.toml:0` missing-version: donkey.publication: "
        "set version \\| in \\[donkey.publication\\]" in summary
    )
    assert "did not finish" not in summary
    assert "not_attempted=1" in github["output"].read_text()


def test_a_failure_with_no_stderr_annotates_each_json_issue(
    github: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("INPUT_DRY_RUN", "true")
    plan = {
        "plan_format": 1,
        "assets": [{**ASSET, "version": None}],
        "issues": [
            {
                "kind": "missing-version",
                "subject": "donkey.publication",
                "path": ".donkey-kit.toml",
                "line": 0,
                "detail": "set version in [donkey.publication]",
            }
        ],
    }
    _donkey(monkeypatch, json.dumps(plan), code=1)

    assert run.main() == 1

    assert capsys.readouterr().out.splitlines() == [
        "::error title=donkey publish::.donkey-kit.toml:0 missing-version: donkey.publication: "
        "set version in [donkey.publication]",
    ]


@pytest.mark.parametrize(
    ("dry_run", "document", "found"),
    [
        (False, {"plan_format": 1}, "plan_format 1"),
        (False, {"publish_format": 2}, "publish_format 2"),
        (True, {"publish_format": 1}, "publish_format 1"),
        (False, {}, "no format key"),
    ],
)
def test_a_document_of_another_format_is_not_read_and_fails_the_step(
    github: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
    document: dict[str, Any],
    found: str,
) -> None:
    """#1427: the summary and the outputs come only from the document this action reads."""
    monkeypatch.setenv("INPUT_DRY_RUN", str(dry_run).lower())
    asset = {**ASSET, "status": "published", "detail": "published 1.0.0"}
    _donkey(monkeypatch, json.dumps({**document, "assets": [asset], "issues": []}))

    assert run.main() == 1

    key = "plan_format" if dry_run else "publish_format"
    (line,) = capsys.readouterr().out.splitlines()
    assert line.startswith(
        f"::error title=donkey publish::donkey printed a JSON document with {found}"
    )
    assert f"this action reads {key} 1" in line
    assert not github["summary"].exists()
    assert not github["output"].exists()


def test_a_failed_run_of_another_format_keeps_donkeys_exit_code(
    github: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _donkey(monkeypatch, json.dumps({"assets": [], "issues": []}), stderr="boom\n", code=2)

    assert run.main() == 2

    assert capsys.readouterr().out.splitlines()[-1] == "::error title=donkey publish::boom"


def test_the_formats_are_donkey_kits() -> None:
    from donkey_kit.registry.exchange_publish import PUBLISH_FORMAT
    from donkey_kit.registry.publish_plan import PLAN_FORMAT

    assert run.FORMATS == {
        True: ("plan_format", PLAN_FORMAT),
        False: ("publish_format", PUBLISH_FORMAT),
    }


def test_the_outputs_are_one_count_per_publish_status_and_the_result() -> None:
    """#1428: a count per status donkey-kit reports, and no other (no ``skipped``)."""
    from donkey_kit.registry.exchange_publish import PublishStatus

    assert run.STATUSES == get_args(PublishStatus)
    action = yaml.safe_load((ACTION / "action.yml").read_text(encoding="utf-8"))
    assert {name: output["value"] for name, output in action["outputs"].items()} == {
        **{
            status.replace("_", "-"): f"${{{{ steps.publish.outputs.{status} }}}}"
            for status in run.STATUSES
        },
        "result": "${{ steps.publish.outputs.result }}",
    }


def test_output_that_is_not_json_still_fails_with_an_annotation(
    github: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _donkey(monkeypatch, "", code=2)

    assert run.main() == 2

    assert capsys.readouterr().out == "::error title=donkey publish::donkey publish exited 2\n"
    assert not github["summary"].exists()


def test_escape_covers_the_workflow_command_characters() -> None:
    assert run.escape("50%\r\nnext") == "50%25%0D%0Anext"


def test_donkeys_stderr_is_echoed_with_workflow_commands_stopped(
    github: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#1442: a line in donkey's stderr is printed, not run as a workflow command."""
    stderr = "::add-mask::nothing\n::set-output name=published::99"
    _donkey(
        monkeypatch, json.dumps({"publish_format": 1, "assets": [], "issues": []}), stderr=stderr
    )

    assert run.main() == 0

    stop, *echoed, resume = capsys.readouterr().out.splitlines()
    token = stop.removeprefix("::stop-commands::")
    assert re.fullmatch("[0-9a-f]{32}", token)
    assert echoed == stderr.splitlines()
    assert resume == f"::{token}::"


def test_each_run_stops_commands_under_a_new_token(
    github: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _donkey(monkeypatch, json.dumps({"publish_format": 1, "assets": [], "issues": []}), stderr="x")
    tokens = set()
    for _ in range(2):
        assert run.main() == 0
        tokens.add(capsys.readouterr().out.splitlines()[0])

    assert len(tokens) == 2


def test_the_echo_ends_its_last_line_and_skips_an_empty_stderr() -> None:
    assert run.echo("no newline", "t") == "::stop-commands::t\nno newline\n::t::\n"
    assert run.echo("", "t") == ""


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "mcp | x"),
        ("asset_id", "a`b|c"),
        ("group_id", "g\n### heading"),
        ("version", "``1``"),
        ("digest", "sha256:| injected |"),
    ],
)
def test_no_asset_field_breaks_the_dry_run_table(field: str, value: str) -> None:
    """#1442: the summary's fields come from the scanned repository."""
    text = run.summary({"assets": [{**ASSET, field: value}], "issues": []}, dry_run=True)
    *_, separator, row = text.splitlines()

    assert separator == "| --- | --- | --- |"
    assert len(re.findall(r"(?<!\\)\|", row)) == 4  # three cells, no more


def test_a_publish_row_escapes_every_field() -> None:
    asset = {
        **ASSET,
        "status": "<b>published</b>",
        "type": "[mcp](https://example.test)",
        "detail": "*done* \\| ~x~",
    }
    row = run.summary({"assets": [asset], "issues": []}, dry_run=False).splitlines()[4]

    assert row == (
        "| \\<b\\>published\\</b\\> | \\[mcp\\](https://example.test) "
        "| `org-1:sku-manager:1.0.0` | \\*done\\* \\\\\\| \\~x\\~ |"
    )


def test_an_issue_escapes_every_field() -> None:
    issue = {
        "kind": "<img src=x>",
        "subject": "![a](b)",
        "path": "app`s.py",
        "line": 3,
        "detail": "line one\nline two",
    }
    text = run.summary({"assets": [], "issues": [issue]}, dry_run=True)

    assert text.splitlines()[-1] == (
        "- ``app`s.py:3`` \\<img src=x\\>: !\\[a\\](b): line one line two"
    )


@pytest.mark.parametrize(
    ("value", "span"),
    [
        ("plain", "`plain`"),
        ("a`b", "``a`b``"),
        ("`edge`", "`` `edge` ``"),
        ("a|b", "`a\\|b`"),
    ],
)
def test_a_code_span_survives_backticks_and_pipes(value: str, span: str) -> None:
    assert run._code(value) == span


def test_the_client_secret_is_masked_before_any_other_step() -> None:
    """#1442: the mask is the action's first step, and the one that runs donkey
    only reads the value after it."""
    steps = yaml.safe_load((ACTION / "action.yml").read_text(encoding="utf-8"))["runs"]["steps"]
    first, *rest = steps
    assert first["env"] == {"CLIENT_SECRET": "${{ inputs.client-secret }}"}
    assert 'echo "::add-mask::$CLIENT_SECRET"' in first["run"]
    readers = [s.get("id") for s in rest if "inputs.client-secret" in yaml.safe_dump(s)]
    assert readers == ["publish"]

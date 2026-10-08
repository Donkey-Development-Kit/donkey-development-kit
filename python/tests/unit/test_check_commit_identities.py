"""``scripts/check_commit_identities.py``, the CI rule from #1048.

A placeholder identity on a PR commit becomes a ``Co-authored-by`` trailer on
``develop`` when the PR is squash-merged, crediting whichever GitHub account
claimed the email. The parsing tests inject the log; one test runs real ``git``
in a temporary repository to pin the ``--format`` string.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_commit_identities.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ddk_check_commit_identities", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered first: dataclasses resolve the module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load()


def _record(
    author: str = "Tommaso Bolis <tbolis@mulesoft.com>",
    committer: str = "GitHub <noreply@github.com>",
    co_authors: tuple[str, ...] = (),
    sha: str = "0123456789abcdef",
) -> str:
    def split(identity: str) -> list[str]:
        name, _, email = identity.partition(" <")
        return [name, email.rstrip(">")]

    fields = [sha, *split(author), *split(committer), "\x1e".join(co_authors)]
    return "\x1f".join(fields) + "\x00\n"


@pytest.mark.parametrize(
    ("name", "email"),
    [
        ("Tommaso Bolis", "tbolis@mulesoft.com"),
        ("Amir Khan", "86777111+amirkhan-ak-sf@users.noreply.github.com"),
        ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"),
        ("GitHub", "noreply@github.com"),
        ("Claude Opus 5.5 (1M context)", "noreply@anthropic.com"),
        ("Cursor", "cursoragent@cursor.com"),
    ],
)
def test_real_contributors_pass(name: str, email: str) -> None:
    assert check.identity_problem(name, email) is None


@pytest.mark.parametrize(
    ("name", "email"),
    [
        ("x", "x@x"),
        ("x", "a@b.c"),
        ("Real Name", "x@x"),
        ("Real Name", "a@b.c"),
        ("Real Name", "dev@example.com"),
        ("Real Name", "dev@ci.test"),
        ("Real Name", "dev@host.localhost"),
        ("Real Name", "dev@mail.invalid"),
        ("Real Name", "no-at-sign"),
        ("Real Name", "@mulesoft.com"),
        ("Real Name", ""),
        ("", "dev@mulesoft.com"),
        ("x", "dev@mulesoft.com"),
    ],
)
def test_placeholder_identities_fail(name: str, email: str) -> None:
    assert check.identity_problem(name, email) is not None


def test_a_placeholder_co_author_fails_a_real_authored_commit() -> None:
    log = _record(co_authors=("x <x@x>", "Claude Opus 5.5 <noreply@anthropic.com>"))

    found = check.problems(check.parse_log(log))

    assert len(found) == 1
    assert "Co-authored-by x <x@x>" in found[0]


def test_placeholder_author_and_committer_each_reported() -> None:
    log = _record(author="x <x@x>", committer="x <a@b.c>")

    found = check.problems(check.parse_log(log))

    assert [message.split(": ")[1] for message in found] == [
        "author x <x@x>",
        "committer x <a@b.c>",
    ]


def test_unparsable_co_author_trailer_fails() -> None:
    assert check.problems(check.parse_log(_record(co_authors=("just a name",))))


def test_main_exits_1_and_annotates_on_a_placeholder(capsys: pytest.CaptureFixture[str]) -> None:
    clean = _record(sha="a" * 40)
    dirty = _record(sha="b" * 40, author="x <x@x>")

    assert check.main(["base..head"], log=lambda _: clean + dirty) == 1
    assert capsys.readouterr().out.startswith("::error::bbbbbbbbbbbb: author x <x@x>")


def test_main_exits_0_on_clean_history_and_empty_range() -> None:
    assert check.main(["base..head"], log=lambda _: _record()) == 0
    assert check.main(["base..head"], log=lambda _: "") == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_read_log_format_round_trips_through_real_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def git(*args: str, **env: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
            env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), **env},
        ).stdout.strip()

    identity = {
        "GIT_AUTHOR_NAME": "Real Name",
        "GIT_AUTHOR_EMAIL": "real@mulesoft.com",
        "GIT_COMMITTER_NAME": "Real Name",
        "GIT_COMMITTER_EMAIL": "real@mulesoft.com",
    }
    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "base", **identity)
    base = git("rev-parse", "HEAD")
    message = "change\n\nCo-authored-by: x <x@x>\nCo-authored-by: Claude <noreply@anthropic.com>"
    git("commit", "-q", "--allow-empty", "-m", message, **identity)
    monkeypatch.chdir(tmp_path)

    [commit] = check.parse_log(check.read_log(f"{base}..HEAD"))

    assert [(i.role, i.name, i.email) for i in commit.identities] == [
        ("author", "Real Name", "real@mulesoft.com"),
        ("committer", "Real Name", "real@mulesoft.com"),
        ("Co-authored-by", "x", "x@x"),
        ("Co-authored-by", "Claude", "noreply@anthropic.com"),
    ]

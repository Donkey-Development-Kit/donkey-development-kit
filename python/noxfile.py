"""One task runner that mirrors CI (#765, docs/ci.md convention 5).

Every job in ``.github/workflows/ci.yml`` (except the ``ci-ok`` aggregate) is a
session of the same name here, and the workflow's check steps call these
sessions, so the checks a PR runs and the checks you run locally are one list.

Locally, from ``python/`` (``pipx install nox``)::

    nox -l                          # list the sessions
    nox -s typecheck-and-lint test-3.11  # the quick loop: lint, types, one test leg
    nox -s typecheck-and-lint test  # the same, with the whole 3.10-3.14 test matrix
    nox                             # every session: the whole blocking CI gate
    nox -s "adapter-contract(crewai)"   # one leg of a matrix job
    nox -R -s test-3.11             # rerun in the existing venv, skipping installs

Each session that installs something builds its own virtualenv and installs
exactly what its CI job installs (``CI_INSTALLS`` below, with the job's
``-c constraints/...`` lock), so a session's result is the job's result. The
ones that install nothing (new-dependencies, commit-identities,
docs-llms-drift, docs-site, workflow-lint) run git, npm, node, stdlib-only
scripts and the workflow linters on the current interpreter and PATH instead;
docs-site's ``npm ci`` is one of its checks, in CI too. A Python that is not
on your machine is skipped, not failed, unless you pass
``--error-on-missing-interpreters``.

A few steps stay in ci.yml by design, and tests/unit/test_noxfile.py names
each: benchmark's trend alert reads the previous main run's artifact, and
workflow-lint's job downloads actionlint and zizmor, sha256-verified, for its
Linux runner (locally, put the versions that job pins on your PATH). A
``uses:`` action is never a session: pip-audit's job audits the session's pin
list with pypa/gh-action-pip-audit, so locally the session runs pip-audit
itself. dependency-review is GitHub's dependency-graph diff, with no checkout
and nothing to run locally, so its job calls no session and its session only
skips, saying so.

In CI the job installs into the runner's Python itself (that is where its cache
and lock steps live) and then runs ``nox --no-venv --no-install -s <job>``: the
same commands, in the job's environment, with every ``session.install`` a no-op.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import string
import sys
import tempfile
from pathlib import Path

import nox

# The pinned version CI runs (ci.yml `NOX_VERSION`); tests/unit/test_noxfile.py
# keeps the two equal.
nox.needs_version = ">=2026.8.17"


def _drop_nox_tool_env_from_path() -> None:
    """Keep nox's own environment off ``PATH`` when it is a separate tool env.

    ``pipx run`` (with its uv backend) and ``uvx`` put the environment nox runs
    in first on ``PATH``. Under ``--no-venv`` a session's ``python`` would then
    be nox's interpreter, not the job's, and every ``python -m pytest`` would run
    without the job's install. When nox was installed into the project's own
    environment (``donkey_kit`` imports here), that environment is the one the
    sessions want, so ``PATH`` stays as it is.
    """
    if importlib.util.find_spec("donkey_kit") is not None:
        return
    scripts = "Scripts" if os.name == "nt" else "bin"
    own = {Path(sys.executable).parent.resolve(), (Path(sys.prefix) / scripts).resolve()}
    entries = os.environ.get("PATH", "").split(os.pathsep)
    os.environ["PATH"] = os.pathsep.join(
        entry for entry in entries if not entry or Path(entry).resolve() not in own
    )


_drop_nox_tool_env_from_path()

_PYTHON_DIR = Path(__file__).resolve().parent
_WEBSITE_DIR = _PYTHON_DIR.parent / "website"

# Each CI job's install step, verbatim (whitespace aside). tests/unit/
# test_noxfile.py fails when one drifts from ci.yml, and a key per job is what
# makes a new CI job fail until it has a session here. The $VARIABLES are the
# job's matrix env vars; `None` marks a job that installs nothing.
CI_INSTALLS: dict[str, str | None] = {
    "base-only": (
        "python -m pip install --upgrade pip && "
        "pip install -e . --group dev -c constraints/base-only-py3.11.txt"
    ),
    "all-extra-resolves": "python -m pip install --upgrade pip",
    "typecheck-and-lint": (
        "python -m pip install --upgrade pip && "
        'pip install -e ".[llm,cli]" --group dev -c constraints/typecheck-and-lint-py3.11.txt'
    ),
    "new-dependencies": None,
    "commit-identities": None,
    "test": (
        "python -m pip install --upgrade pip && "
        'pip install -e ".[llm,cli,local,otel,a2a]" --group dev '
        '-c "constraints/test-py${PYTHON_VERSION}.txt"'
    ),
    "openai-1x": (
        "python -m pip install --upgrade pip && "
        'pip install -e ".[llm,cli,local,otel]" --group dev "openai<2" '
        "-c constraints/openai-1x-py3.12.txt"
    ),
    "anthropic-stacks": (
        "python -m pip install --upgrade pip && "
        'pip install -e ".[llm,anthropic]" --group dev "$ANTHROPIC_SPEC"'
    ),
    "adk-stacks": (
        'python -m pip install --upgrade pip && pip install -e ".[llm,adk]" --group dev "$ADK_SPEC"'
    ),
    "agent-framework-middleware": (
        "python -m pip install --upgrade pip && "
        'pip install -e ".[llm,agent_framework]" --group dev '
        "-c constraints/agent-framework-middleware-py3.12.txt"
    ),
    "llamaindex-transport": (
        "python -m pip install --upgrade pip && "
        'pip install -e ".[llm,llamaindex]" --group dev '
        "-c constraints/llamaindex-transport-py3.12.txt"
    ),
    "agents-strands-last-call": (
        "python -m pip install --upgrade pip && "
        'pip install -e ".[llm,openai-agents,strands]" --group dev '
        "-c constraints/agents-strands-last-call-py3.12.txt"
    ),
    "adapter-contract": (
        "python -m pip install --upgrade pip && "
        'pip install -e ".[llm,local,$DONKEY_CONTRACT_EXTRA]" --group dev '
        '-c "constraints/adapter-contract-${DONKEY_CONTRACT_EXTRA}-py3.12.txt"'
    ),
    "benchmark": (
        "python -m pip install --upgrade pip && "
        'pip install -e ".[llm,cli,local,otel]" --group dev -c constraints/benchmark-py3.11.txt'
    ),
    "quickstart": 'pip install -e ".[llm,local,otel]" -c constraints/quickstart-py3.11.txt',
    "langgraph-demo": (
        "python -m pip install --upgrade pip && "
        'pip install -e ".[llm,langgraph,local,otel]" --group dev '
        "-c constraints/langgraph-demo-py3.11.txt"
    ),
    "openai-agents-demo": (
        "python -m pip install --upgrade pip && "
        'pip install -e ".[llm,openai-agents,local,otel]" --group dev '
        "-c constraints/openai-agents-demo-py3.11.txt"
    ),
    "docs-llms-drift": None,
    "docs-site": None,
    "pip-audit": "python -m pip install --upgrade pip",
    "dependency-review": None,
    "workflow-lint": None,
}

# The matrices of ci.yml's matrix jobs, in its order (test_noxfile.py checks).
# The ids are the matrix values, so CI names a leg as "<job>($VAR)". Both
# anthropic legs map to one venv directory (.nox/anthropic-stacks-anthropic-1);
# installs rerun per leg, so only -R (no reinstall) across the two is unsafe.
TEST_PYTHONS = ["3.10", "3.11", "3.12", "3.13", "3.14"]
ANTHROPIC_SPECS = ["anthropic>=1", "anthropic<1"]
ADK_SPECS = ["google-adk>=2.4", "google-adk==2.4.0"]
CONTRACT_EXTRAS = [
    "langgraph",
    "adk",
    "strands",
    "agent_framework",
    "openai-agents",
    "anthropic",
    "crewai",
    "llamaindex",
]
# pip-audit's matrix `include`: each leg's target (its id) and the spec it resolves.
AUDIT_SPECS = {"base": ".", "all": ".[all]"}

# A wall-clock bound, as CI's `timeout N cmd`, that also works where coreutils'
# `timeout` does not exist (macOS): run argv[2:] under this interpreter.
_BOUNDED = (
    "import subprocess, sys; "
    "sys.exit(subprocess.run([sys.executable, *sys.argv[2:]], timeout=float(sys.argv[1]))"
    ".returncode)"
)

# #730: a plain install carries no pydantic.
_NO_PYDANTIC = """\
import importlib.metadata, sys
try:
    importlib.metadata.distribution("pydantic")
except importlib.metadata.PackageNotFoundError:
    pass
else:
    sys.exit("pydantic is installed by the dev group; the base install must not need it")
import donkey_kit, donkey_kit.experimental
assert "pydantic" not in sys.modules
"""

# BG §1.4, #746: load() reads the installed wheel's package data, not a checkout.
_WHEEL_FIXTURES = """\
from donkey_kit.simulator import fixtures

assert "site-packages" in fixtures.LOCK_PATH.parts, fixtures.LOCK_PATH
for shape in fixtures.SHAPES:
    fixtures.load(shape)
assert fixtures.LOCK_PATH.exists(), fixtures.LOCK_PATH
assert fixtures.compute_manifest() == fixtures.read_lock()
before = fixtures.LOCK_PATH.read_bytes()
fixtures.write_lock()
assert fixtures.LOCK_PATH.read_bytes() == before
"""

_DEAD = "http://127.0.0.1:9"  # a closed port: any request through it fails


def _install(session: nox.Session, job: str, **variables: str) -> None:
    """Run ``job``'s CI install step in the session's venv (a no-op in CI)."""
    template = CI_INSTALLS[job]
    if template is None:
        return
    command = string.Template(template).substitute(variables)
    for part in command.split("&&"):
        args = shlex.split(part)
        if args[:4] == ["python", "-m", "pip", "install"]:
            session.install(*args[4:])
        elif args[:2] == ["pip", "install"]:
            session.install(*args[2:])
        else:  # CI_INSTALLS holds pip installs only
            session.error(f"not a pip install step: {part!r}")


def _python_version(session: nox.Session) -> str:
    """The session interpreter's ``major.minor``.

    Under ``--no-venv`` nox ignores the ``python=`` list, so the name says
    nothing and the interpreter on PATH is asked instead.
    """
    if isinstance(session.python, str):
        return session.python
    # stderr=None: nox merges stderr into the captured output by default, so an
    # interpreter warning would turn "3.11" into something else and the 3.11
    # leg would skip coverage while staying green (#751).
    out = session.run(
        "python",
        "-c",
        "import sys; print('%d.%d' % sys.version_info[:2])",
        silent=True,
        stderr=None,
    )
    version = str(out).strip()
    if version not in TEST_PYTHONS:
        session.error(f"unexpected interpreter {version!r}, expected one of {TEST_PYTHONS}")
    return version


def _bounded(session: nox.Session, seconds: int, *args: str) -> None:
    """Run ``python *args`` and fail it after ``seconds`` of wall-clock time."""
    session.run("python", "-c", _BOUNDED, str(seconds), *args)


@nox.session(name="base-only", python="3.11")
def base_only(session: nox.Session) -> None:
    """The base package alone imports and passes the unit suite; the wheel ships fixtures."""
    _install(session, "base-only")
    session.run(
        "python",
        "-c",
        "import donkey_kit; from donkey_kit import Donkey; print(donkey_kit.__version__)",
    )
    session.run("python", "-c", _NO_PYDANTIC)
    session.run("pytest", "-q", "tests/unit")
    # The installed-wheel check runs in its own environment. The build uses the
    # locked release toolchain, as publish-*.yml does (#763): --no-isolation
    # builds with the hatchling installed here, not the newest one. It is
    # installed with run(), not install(), so it is installed in CI's
    # --no-install run too.
    with tempfile.TemporaryDirectory() as wheel_dir, tempfile.TemporaryDirectory() as venv_dir:
        toolchain = ("-c", "constraints/release-py3.11.txt", "build", "hatchling")
        if session.run("python", "-m", "pip", "install", *toolchain) is None:
            return  # --install-only
        session.run("python", "-m", "build", "--no-isolation", "--wheel", "--outdir", wheel_dir)
        wheels = sorted(Path(wheel_dir).glob("*.whl"))
        if len(wheels) != 1:
            session.error(f"expected exactly one wheel, built {wheels}")
        wheel = str(wheels[0])
        # #821: the wheel ships fixtures; none may carry a tenant identifier.
        session.run("python", "scripts/scrub_fixtures.py", "--check", wheel)
        session.run("python", "-m", "venv", venv_dir)
        bindir = Path(venv_dir) / ("Scripts" if os.name == "nt" else "bin")
        session.run(str(bindir / "pip"), "install", wheel, external=True)
        session.run(str(bindir / "python"), "-c", _WHEEL_FIXTURES, external=True)


@nox.session(name="all-extra-resolves", python=TEST_PYTHONS)
def all_extra_resolves(session: nox.Session) -> None:
    """A current pip resolves ``donkey-kit[all]`` within five minutes (#697)."""
    _install(session, "all-extra-resolves")
    _bounded(session, 300, "-m", "pip", "install", "--dry-run", ".[all]")


@nox.session(name="typecheck-and-lint", python="3.11")
def typecheck_and_lint(session: nox.Session) -> None:
    """mypy --strict, ruff, import-linter, the repo's own checks and vulture."""
    _install(session, "typecheck-and-lint")
    session.run("mypy")
    # #721: the examples are user-facing code, so they get the same --strict
    # config, one package per invocation (each is its own program). mypy never
    # follows a framework extra (the follow_imports = "skip" override in
    # pyproject.toml, #763), installed or not, so this gives the same result in
    # a venv with every extra. LangGraph is the exception: it is not in that
    # override and is checked against the real package in langgraph-demo.
    for example in sorted((_PYTHON_DIR / "examples").iterdir()):
        if not example.is_dir() or example.name[0] in "._" or example.name == "langgraph":
            continue
        session.run("mypy", "-p", f"examples.{example.name}")
    session.run("ruff", "check", ".")
    session.run("ruff", "format", "--check", ".")
    session.run("lint-imports")  # framework-free core, §1.1
    session.run("python", "scripts/check_verification_claims.py")  # #718
    session.run("python", "scripts/check_doc_links.py")  # #785
    session.run("python", "scripts/scrub_fixtures.py", "--check")  # #821
    session.run("vulture")  # #720


@nox.session(name="new-dependencies", venv_backend="none")
def new_dependencies(session: nox.Session) -> None:
    """A direct dependency new to this branch exists on PyPI (#936).

    Stdlib-only, so it runs on the interpreter on PATH. CI passes
    ``-- --base <file>``; locally the base is origin/develop (``git fetch`` first).
    """
    if session.posargs:
        session.run("python", "scripts/check_new_dependencies.py", *session.posargs)
        return
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base-pyproject.toml"
        # stderr=None keeps git's messages out of the captured file content.
        text = session.run(
            "git",
            "show",
            "origin/develop:python/pyproject.toml",
            silent=True,
            stderr=None,
            external=True,
        )
        if text is None:
            return  # --install-only
        base.write_text(str(text), encoding="utf-8")
        session.run("python", "scripts/check_new_dependencies.py", "--base", str(base))


@nox.session(name="commit-identities", venv_backend="none")
def commit_identities(session: nox.Session) -> None:
    """No placeholder git identity in this branch's commits (#1048).

    Stdlib-only. CI passes ``-- BASE..HEAD``; locally the range is
    ``origin/develop..HEAD``.
    """
    revisions = session.posargs or ["origin/develop..HEAD"]
    session.run("python", "scripts/check_commit_identities.py", *revisions)


@nox.session(name="test", python=TEST_PYTHONS)
def test(session: nox.Session) -> None:
    """The default suite, the simulator boot, the offline seams, and coverage on 3.11."""
    version = _python_version(session)
    _install(session, "test", PYTHON_VERSION=version)
    # #751: coverage is measured on the 3.11 leg only; `[run] patch` in
    # pyproject.toml also measures the conformance tests' pytest subprocesses.
    coverage = version == "3.11"
    pytest = ["coverage", "run", "-m", "pytest", "-q"] if coverage else ["pytest", "-q"]
    if coverage:
        session.run("coverage", "erase")
    session.run(*pytest)
    # BG §1.4: the simulator's out-of-process boot, deselected from the default run.
    session.run(*pytest, "-m", "local_gateway")
    # #801: the offline seams stay offline behind a dead corporate proxy.
    session.run(
        "pytest",
        "-q",
        "tests/unit/test_simulate.py",
        "tests/unit/test_conformance.py",
        "tests/unit/test_conformance_plugin.py",
        "tests/unit/test_transport_proxy_mounts.py",
        env={"HTTPS_PROXY": _DEAD, "HTTP_PROXY": _DEAD, "ALL_PROXY": _DEAD},
    )
    # #747: the whole default suite stays green with a dead proxy and collector.
    session.run("pytest", "-q", env={"HTTPS_PROXY": _DEAD, "OTEL_EXPORTER_OTLP_ENDPOINT": _DEAD})
    if not coverage:
        return
    # The report prints before `fail_under` (pyproject.toml) fails it, so a miss
    # still shows the numbers. In CI it goes to the job summary.
    session.run("coverage", "combine")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        session.run("coverage", "report")
        return
    with open(summary, "a", encoding="utf-8") as out:
        out.write("### Coverage (branch, python 3.11 leg)\n\n")
        out.flush()
        session.run("coverage", "report", "--format=markdown", stdout=out)


@nox.session(name="openai-1x", python="3.12")
def openai_1x(session: nox.Session) -> None:
    """The default and local_gateway suites on openai 1.x, which the `llm` floor allows (#769)."""
    _install(session, "openai-1x")
    session.run(
        "python",
        "-c",
        "import openai; assert openai.__version__.startswith('1.'), openai.__version__",
    )
    session.run("pytest", "-q")
    session.run("pytest", "-q", "-m", "local_gateway")  # BG §1.4


def _framework_tests(session: nox.Session, extra: str, *files: str) -> None:
    # #748: with the framework installed, a skip on it is a failure.
    session.env["DONKEY_CONTRACT_EXTRA"] = extra
    session.run("pytest", "-q", *files)


@nox.session(name="anthropic-stacks", python="3.12")
@nox.parametrize("spec", ANTHROPIC_SPECS, ids=ANTHROPIC_SPECS)
def anthropic_stacks(session: nox.Session, spec: str) -> None:
    """Both anthropic majors: the httpx2 bridge on >=1, the shared client on <1 (#701)."""
    _install(session, "anthropic-stacks", ANTHROPIC_SPEC=spec)
    _framework_tests(
        session,
        "anthropic",
        "tests/unit/test_anthropic_client_stacks.py",
        "tests/unit/test_anthropic_httpx2_bridge.py",
        "tests/unit/test_adapter_ergonomics.py",
        "tests/unit/test_adapter_async_last_call.py",
        "tests/unit/test_default_runtime.py",
        "tests/unit/test_shared_client_views.py",
        "tests/conformance/test_typed_refusal_bridge.py",
    )


@nox.session(name="adk-stacks", python="3.12")
@nox.parametrize("spec", ADK_SPECS, ids=ADK_SPECS)
def adk_stacks(session: nox.Session, spec: str) -> None:
    """google-adk at its floor and its newest release (#735)."""
    _install(session, "adk-stacks", ADK_SPEC=spec)
    _framework_tests(
        session,
        "adk",
        "tests/unit/test_adapter_ergonomics.py",
        "tests/unit/test_framework_redirects.py",
        "tests/unit/test_adapter_async_last_call.py",
        "tests/unit/test_transport_injection.py",
        "tests/conformance/test_typed_refusal_bridge.py",
    )


@nox.session(name="agent-framework-middleware", python="3.12")
def agent_framework_middleware(session: nox.Session) -> None:
    """policy_middleware() on a real Agent Framework Agent (#739, #740)."""
    _install(session, "agent-framework-middleware")
    _framework_tests(
        session,
        "agent_framework",
        "tests/unit/test_agent_framework_policy_middleware.py",
        "tests/unit/test_adapter_ergonomics.py",
        "tests/unit/test_transport_injection.py",
        "tests/conformance/test_typed_refusal_bridge.py",
    )


@nox.session(name="llamaindex-transport", python="3.12")
def llamaindex_transport(session: nox.Session) -> None:
    """LlamaIndex sends through the shared client (#740)."""
    _install(session, "llamaindex-transport")
    _framework_tests(
        session,
        "llamaindex",
        "tests/unit/test_transport_injection.py",
        "tests/unit/test_adapter_ergonomics.py",
        "tests/conformance/test_typed_refusal_bridge.py",
    )


@nox.session(name="agents-strands-last-call", python="3.12")
def agents_strands_last_call(session: nox.Session) -> None:
    """donkey.last_call for the OpenAI Agents SDK and Strands (#899)."""
    _install(session, "agents-strands-last-call")
    _framework_tests(
        session,
        "openai-agents,strands",
        "tests/unit/test_adapter_async_last_call.py",
        "tests/unit/test_adapter_ergonomics.py",
        "tests/conformance/test_typed_refusal_bridge.py",
    )


@nox.session(name="adapter-contract", python="3.12")
@nox.parametrize("extra", CONTRACT_EXTRAS, ids=CONTRACT_EXTRAS)
def adapter_contract(session: nox.Session, extra: str) -> None:
    """The whole suite and the strict example smoke, one ADAPTERS extra installed (#742)."""
    _install(session, "adapter-contract", DONKEY_CONTRACT_EXTRA=extra)
    session.env["DONKEY_CONTRACT_EXTRA"] = extra
    # #748: the whole suite, not a file list, so every test that needs this
    # leg's framework runs against the real install instead of skipping.
    session.run("python", "-m", "pytest", "-q")
    # ADAPTERS keys use underscores where extras use hyphens (openai-agents).
    session.run("python", "scripts/smoke_example.py", extra.replace("-", "_"))


@nox.session(name="benchmark", python="3.11")
def benchmark(session: nox.Session) -> None:
    """The GenAI span adds < 1 ms per governed call (BG §1.6, #194)."""
    _install(session, "benchmark")
    session.run("pytest", "-q", "-m", "benchmark", "-s")


@nox.session(name="quickstart", python="3.11")
def quickstart(session: nox.Session) -> None:
    """The gateway-free quickstart exits 0 with no credentials (#203, BG §1.4)."""
    _install(session, "quickstart")
    _bounded(session, 120, "examples/quickstart/main.py")


@nox.session(name="langgraph-demo", python="3.11")
def langgraph_demo(session: nox.Session) -> None:
    """Scenario A end to end against the simulator, then through conformance (#199, BG §1.8)."""
    _install(session, "langgraph-demo")
    session.run("mypy", "-p", "examples.langgraph")
    _bounded(session, 120, "-m", "examples.langgraph.main")
    # `python -m pytest` puts python/ on sys.path, so `examples` imports.
    session.run(
        "python",
        "-m",
        "pytest",
        "-q",
        "--donkey-conformance",
        "--donkey-agent=examples.langgraph.main:build",
    )


@nox.session(name="openai-agents-demo", python="3.11")
def openai_agents_demo(session: nox.Session) -> None:
    """The second deep adapter's Scenario A, then through conformance (#223, BG §1.8)."""
    _install(session, "openai-agents-demo")
    # No mypy step: agents.* is follow_imports = "skip", so typecheck-and-lint
    # already covers examples.openai_agents (unlike langgraph-demo, #721).
    _bounded(session, 120, "-m", "examples.openai_agents.main")
    session.run(
        "python",
        "-m",
        "pytest",
        "-q",
        "--donkey-conformance",
        "--donkey-agent=examples.openai_agents.main:build",
    )


@nox.session(name="docs-llms-drift", venv_backend="none")
def docs_llms_drift(session: nox.Session) -> None:
    """The website's unit tests pass and the committed llms artifacts are current (#205)."""
    session.chdir(_WEBSITE_DIR)
    session.run("npm", "test", external=True)
    session.run("node", "scripts/generate-llms.mjs", external=True)
    stale = session.run(
        "git", "status", "--porcelain", "public", silent=True, stderr=None, external=True
    )
    if stale is None:
        return  # --install-only
    if str(stale).strip():
        # A line starting with ::error:: is an annotation on the CI run.
        print(
            "::error::llms artifacts are out of date. "
            "Run 'npm run generate:llms' in website/ and commit the result."
        )
        print(stale, end="")
        session.run("git", "--no-pager", "diff", "--", "public", external=True)
        session.error("llms artifacts are out of date")
    session.log("llms artifacts are up to date.")


# The project-prefix base path the site deploys under (docs-site, #759).
_DOCS_BASE_PATH = "/donkey-development-kit"
# Unset for a command: only the docs-site build needs the GitHub token.
_NO_GITHUB_TOKEN: dict[str, str | None] = {"GITHUB_TOKEN": None}


@nox.session(name="docs-site", venv_backend="none")
def docs_site(session: nox.Session) -> None:
    """The docs site type-checks, lints, builds and has no dead internal link (#759).

    Runs ``npm ci`` first, as the job does, so it replaces ``website/node_modules``.
    The build's prebuild regenerates ``data/roadmap.json`` from the GitHub API
    (``GITHUB_TOKEN`` if set) and keeps the committed snapshot if it can't reach it.
    """
    session.chdir(_WEBSITE_DIR)
    session.run("npm", "ci", env=_NO_GITHUB_TOKEN, external=True)
    session.run("npm", "run", "typecheck", env=_NO_GITHUB_TOKEN, external=True)
    session.run("npm", "run", "lint", env=_NO_GITHUB_TOKEN, external=True)
    # The static export with the project-prefix base path, as preview:pages builds it.
    session.run("npm", "run", "build", env={"DOCS_BASE_PATH": _DOCS_BASE_PATH}, external=True)
    # Internal links and anchors in out/.
    session.run(
        "npm",
        "run",
        "check:links",
        env={"DOCS_BASE_PATH": _DOCS_BASE_PATH, **_NO_GITHUB_TOKEN},
        external=True,
    )


@nox.session(name="pip-audit", python="3.12")
@nox.parametrize("target", list(AUDIT_SPECS), ids=list(AUDIT_SPECS))
def pip_audit(session: nox.Session, target: str) -> None:
    """No known vulnerability in a fresh resolve of the base install or ``[all]`` (#757).

    Fresh, not locked: the question is what ``pip install donkey-kit`` pulls in
    today. pip only resolves (``--dry-run --report``), so nothing is installed,
    and the pinned set minus donkey-kit itself is audited with ``--no-deps``.
    CI passes ``-- <file>``: the session writes the pin list there, and the
    job's pypa/gh-action-pip-audit step audits it. Without a path the session
    audits the list itself, with the pip-audit range that action installs.
    """
    _install(session, "pip-audit")
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "resolve.json"
        _bounded(
            session,
            300,
            *("-m", "pip", "install", "--dry-run", "--ignore-installed", "--quiet"),
            *("--report", str(report), AUDIT_SPECS[target]),
        )
        if not report.is_file():
            return  # --install-only
        resolved = json.loads(report.read_text(encoding="utf-8"))["install"]
        pins = "".join(
            f"{dist['metadata']['name']}=={dist['metadata']['version']}\n"
            for dist in resolved
            if dist["metadata"]["name"] != "donkey-kit"
        )
        print(pins, end="")
        requirements = Path(session.posargs[0]) if session.posargs else Path(tmp) / "pins.txt"
        requirements.write_text(pins, encoding="utf-8")
        if session.posargs:
            return  # CI: the pip-audit action audits the list
        session.install("pip-audit~=2.0,>=2.5.6")
        session.run("pip-audit", "--requirement", str(requirements), "--no-deps")


@nox.session(name="dependency-review", venv_backend="none")
def dependency_review(session: nox.Session) -> None:
    """GitHub-only: a PR's new dependencies, for known vulnerabilities and licences (#757).

    The job runs actions/dependency-review-action on the dependency graph diff
    GitHub keeps between a PR's base and head. It has no checkout, so it calls
    no session, and there is nothing to run locally. The session exists so
    every ci.yml job has one; ``nox`` lists it and skips it.
    """
    session.skip("GitHub-only: dependency-review reads the PR's dependency graph diff on GitHub")


@nox.session(name="workflow-lint", venv_backend="none")
def workflow_lint(session: nox.Session) -> None:
    """actionlint and zizmor over .github/ (#757).

    Runs the tools on PATH: in CI, the copies the job fetched and verified by
    sha256; locally, install the ``ACTIONLINT_VERSION`` and ``ZIZMOR_VERSION``
    the workflow-lint job pins. zizmor's online audits read ``GH_TOKEN`` if set;
    actionlint (and the shellcheck it runs) never gets it.
    """
    session.chdir(_PYTHON_DIR.parent)
    session.run("actionlint", "-color", env={"GH_TOKEN": None}, external=True)
    session.run("zizmor", "--format=github", ".github/", external=True)

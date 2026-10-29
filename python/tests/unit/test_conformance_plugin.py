"""End-to-end tests of the shipped pytest plugin (#191, BG §1.5).

These spawn a throwaway pytest session (via the ``pytester`` fixture) that loads
our real ``pytest11`` plugin exactly as a customer's ``pytest --donkey-conformance
--donkey-agent=my_app:build`` would, and assert the customer-visible behaviour:

- inert with no ``--donkey-conformance`` flag (the default footprint is just CLI
  options + an unused fixture);
- a clean ``UsageError`` (exit code 4, no traceback) when ``--donkey-agent`` is missing
  or a ``KNOWN_LIMITATIONS`` entry is invalid — validated at collection, never a
  silent skip (the conformance kit);
- a green run + printed scenario table for a well-behaved agent, and a red run
  naming the tripped scenario for a broken one.

``runpytest_subprocess`` is used throughout: it runs ``python -m pytest`` in a
fresh process, which both auto-loads the installed entry-point plugin and puts
the tmp dir on ``sys.path`` so ``--donkey-agent=<module>:build`` imports the file we
wrote there. The runs that actually build an agent need ``openai`` (the agent
calls ``donkey.openai()``); those tests skip without it. The inert/help and the
two UsageError paths need neither ``openai`` nor a live gateway, so they run
everywhere the base install plus the ``dev`` group does.
"""

from __future__ import annotations

import pytest

# pytest's own exit codes (pytest.ExitCode); spelled out to keep the asserts legible.
_EXIT_OK = 0
_EXIT_TESTS_FAILED = 1
_EXIT_USAGE_ERROR = 4


# A well-behaved agent module: bridges refusals with classify(), never retries,
# logs the correlation id, tolerates an absent budget. Passes all four scenarios.
_GOOD_AGENT = """
import logging
import openai
from donkey_kit.core.errors import classify
from donkey_kit.core.telemetry import current_correlation_id

_LOG = logging.getLogger("customer.agent")


class Agent:
    def __init__(self, donkey):
        self._client = donkey.openai()
        self._budget = donkey.budget

    async def run(self, prompt):
        try:
            resp = await self._client.responses.create(model="gpt-5.1", input=prompt)
        except openai.APIStatusError as exc:
            raise classify(exc.response) from exc
        _LOG.info("done correlation_id=%s", current_correlation_id())
        if self._budget.remaining is not None and self._budget.remaining < 0:
            raise RuntimeError("over budget")
        return resp.output_text


def build(donkey):
    return Agent(donkey)
"""

# The headline bug: retries a terminal 429 budget refusal. Fails the retry scenario.
_RETRY_BUG_AGENT = """
import openai
from donkey_kit.core.errors import classify


class Agent:
    def __init__(self, donkey):
        self._client = donkey.openai()

    async def run(self, prompt):
        for attempt in range(3):
            try:
                resp = await self._client.responses.create(model="gpt-5.1", input=prompt)
                return resp.output_text
            except openai.RateLimitError as exc:
                if attempt == 2:
                    raise classify(exc.response) from exc
        raise AssertionError("unreachable")


def build(donkey):
    return Agent(donkey)
"""

# No openai import on purpose: the invalid-exemption path must fail at
# validation, not because the module could not be imported.
_BAD_KNOWN_LIMITATIONS_AGENT = """
KNOWN_LIMITATIONS = {"not_a_real_scenario": "this key names no scenario"}


def build(donkey):
    return object()
"""


def test_plugin_is_inert_without_the_flag(pytester: pytest.Pytester) -> None:
    # With no --donkey-conformance, normal collection is untouched: an ordinary
    # test in the project is collected and passes as if the plugin weren't there.
    pytester.makepyfile(test_ordinary="def test_ok():\n    assert True\n")
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)


def test_plugin_imports_only_a_handful_of_sdk_modules(pytester: pytest.Pytester) -> None:
    # The plugin loads on every pytest run in the environment (#746), so a session
    # without --donkey-conformance must not drag the SDK onto the import path.
    pytester.makepyfile(
        test_footprint=(
            "import sys\n"
            "def test_footprint():\n"
            "    loaded = {m for m in sys.modules if m.startswith('donkey_kit')}\n"
            "    assert loaded <= {\n"
            "        'donkey_kit', 'donkey_kit.conformance', 'donkey_kit.conformance.plugin',\n"
            "        'donkey_kit.conformance.report', 'donkey_kit.conformance.suite',\n"
            "    }, sorted(loaded)\n"
        )
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)


def test_every_plugin_option_is_donkey_prefixed(pytester: pytest.Pytester) -> None:
    # pytest refuses to start when two plugins register one option name, so the
    # plugin's own options are namespaced; --agent is only a deprecated alias.
    config = pytester.parseconfig()
    names = {name for opt in config._parser.getgroup("donkey").options for name in opt.names()}
    assert "--donkey-agent" in names
    assert {n for n in names if not n.startswith("--donkey-")} == {"--agent"}


def test_agent_alias_still_works_with_a_deprecation_warning(
    pytester: pytest.Pytester,
) -> None:
    pytest.importorskip("openai")
    pytester.makepyfile(goodagent=_GOOD_AGENT)
    result = pytester.runpytest_subprocess("--donkey-conformance", "--agent=goodagent:build")
    assert result.ret == _EXIT_OK
    result.stdout.fnmatch_lines(["*DeprecationWarning: --agent is deprecated; use --donkey-agent*"])


def test_agent_alias_yields_when_another_plugin_owns_it(pytester: pytest.Pytester) -> None:
    # A plugin that registered --agent first must not stop pytest from starting,
    # and --donkey-agent still reaches the conformance plugin.
    pytester.makepyfile(
        theirplugin=(
            "def pytest_addoption(parser):\n"
            "    parser.addoption('--agent', action='store', default=None)\n"
        ),
        test_ordinary="def test_ok():\n    assert True\n",
    )
    result = pytester.runpytest_subprocess("-p", "theirplugin", "--agent=theirs")
    result.assert_outcomes(passed=1)
    result = pytester.runpytest_subprocess("-p", "theirplugin", "--donkey-conformance")
    assert result.ret == _EXIT_USAGE_ERROR
    result.stderr.fnmatch_lines(["*requires --donkey-agent*"])


def test_options_are_registered(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    # The plugin auto-loads via its entry point, so its options appear in --help
    # even though it stays inert. Proves the plugin is present in the subprocess.
    # The subprocess inherits the environment, and FORCE_COLOR colours the help.
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    result = pytester.runpytest_subprocess("--help")
    result.stdout.fnmatch_lines(["*--donkey-conformance*"])
    result.stdout.fnmatch_lines(["*--donkey-agent=MODULE:FACTORY*"])


def test_donkey_fixture_is_available(pytester: pytest.Pytester) -> None:
    # The `donkey` fixture is offered on every run (built from an offline config),
    # so a customer can drive donkey.simulate(...) in their own tests.
    pytester.makepyfile(
        test_uses_donkey=(
            "def test_has_donkey(donkey):\n"
            "    assert donkey is not None\n"
            "    assert hasattr(donkey, 'simulate')\n"
        )
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)


def test_gateway_fixture_is_registered(pytester: pytest.Pytester) -> None:
    # The out-of-process `gateway` fixture (#278) is offered on every run, beside
    # `donkey`. Asserted via --fixtures so it proves registration without booting
    # a server (which would need the [local] extra); its docstring names its shape.
    result = pytester.runpytest_subprocess("--fixtures", "-v")
    result.stdout.fnmatch_lines(["*gateway*"])
    result.stdout.fnmatch_lines(["*ephemeral port*"])


def test_classify_registry_fixture_is_donkey_prefixed_and_isolates(
    pytester: pytest.Pytester,
) -> None:
    # The plugin loads on every run, so its registry fixture is namespaced like
    # its options (#1425); the unprefixed name never shipped, so it has no alias.
    # A registration made through the fixture is gone in the next test.
    pytester.makepyfile(
        test_registry=(
            "import pytest\n"
            "import donkey_kit\n"
            "from donkey_kit import PolicyViolation\n"
            "class SalaryLeakBlocked(PolicyViolation):\n"
            "    policy = 'acme-salary-block'\n"
            "    remediation = 'Remove the salary figures.'\n"
            "def test_registers(donkey_classify_registry):\n"
            "    assert donkey_classify_registry is donkey_kit.classify\n"
            "    donkey_classify_registry.register(\n"
            "        match={'status': 403, 'error_type': 'acme_salary_block'},\n"
            "        raises=SalaryLeakBlocked,\n"
            "    )\n"
            "    assert len(donkey_kit.classify.registered()) == 1\n"
            "def test_did_not_leak():\n"
            "    assert donkey_kit.classify.registered() == ()\n"
            "def test_old_name_is_gone(request):\n"
            "    with pytest.raises(pytest.FixtureLookupError):\n"
            "        request.getfixturevalue('classify_registry')\n"
        )
    )
    result = pytester.runpytest_subprocess("-p", "no:randomly")
    result.assert_outcomes(passed=3)


def test_conformance_without_agent_is_a_usage_error(pytester: pytest.Pytester) -> None:
    # --donkey-conformance with no --donkey-agent is a misuse: a clean UsageError
    # (exit 4), not a traceback and not a silently-empty run.
    result = pytester.runpytest_subprocess("--donkey-conformance")
    assert result.ret == _EXIT_USAGE_ERROR
    result.stderr.fnmatch_lines(["*--donkey-agent*"])


def test_invalid_known_limitations_is_a_usage_error(pytester: pytest.Pytester) -> None:
    # A KNOWN_LIMITATIONS key that names no scenario is rejected at collection
    # time (the conformance kit: asserted, never silent) — a UsageError before any scenario runs.
    pytester.makepyfile(brokenagent=_BAD_KNOWN_LIMITATIONS_AGENT)
    result = pytester.runpytest_subprocess(
        "--donkey-conformance", "--donkey-agent=brokenagent:build"
    )
    assert result.ret == _EXIT_USAGE_ERROR
    result.stderr.fnmatch_lines(["*unknown scenario*"])


def test_good_agent_passes_all_scenarios_and_prints_the_table(
    pytester: pytest.Pytester,
) -> None:
    pytest.importorskip("openai")
    pytester.makepyfile(goodagent=_GOOD_AGENT)
    result = pytester.runpytest_subprocess(
        "--donkey-conformance", "--donkey-agent=goodagent:build", "-v"
    )
    assert result.ret == _EXIT_OK
    # One pytest item per scenario, all green.
    result.assert_outcomes(passed=len(_scenario_names()))
    # The terminal summary prints the scenario -> status table.
    result.stdout.fnmatch_lines(["*Retries a budget refusal*"])
    result.stdout.fnmatch_lines(["*PASS*"])


def test_retry_bug_agent_fails_the_retry_scenario(pytester: pytest.Pytester) -> None:
    pytest.importorskip("openai")
    pytester.makepyfile(retryagent=_RETRY_BUG_AGENT)
    result = pytester.runpytest_subprocess(
        "--donkey-conformance", "--donkey-agent=retryagent:build", "-v"
    )
    assert result.ret == _EXIT_TESTS_FAILED
    # The retry scenario item is the one that fails, and its finding is shown.
    result.stdout.fnmatch_lines(["*retries_token_budget*FAIL*"])
    result.stdout.fnmatch_lines(["*retried*"])


def test_mode_option_is_registered_and_defaults_to_in_process(
    pytester: pytest.Pytester,
) -> None:
    # #322: --donkey-mode is offered beside the other options, and an absent flag
    # keeps the #191 in-process behaviour.
    result = pytester.runpytest_subprocess("--help")
    result.stdout.fnmatch_lines(["*--donkey-mode*"])
    assert pytester.parseconfig().getoption("donkey_mode") == "in-process"


def test_unknown_mode_is_a_usage_error(pytester: pytest.Pytester) -> None:
    result = pytester.runpytest_subprocess(
        "--donkey-conformance", "--donkey-agent=goodagent:build", "--donkey-mode=docker"
    )
    assert result.ret == _EXIT_USAGE_ERROR
    result.stderr.fnmatch_lines(["*invalid choice*"])


def test_server_mode_without_the_local_extra_names_the_install(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Choosing server mode without starlette/uvicorn is a misuse with a fix, not
    # an ImportError traceback mid-run.
    from donkey_kit.conformance import plugin

    server = pytester.parseconfig("--donkey-mode=server")
    default = pytester.parseconfig()
    monkeypatch.setattr(plugin.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(pytest.UsageError, match=r"donkey-kit\[local\]"):
        plugin._resolve_mode(server)
    # In-process mode never needs the extra.
    assert plugin._resolve_mode(default) == "in-process"


@pytest.mark.local_gateway
def test_server_mode_runs_the_suite_against_a_live_simulator(
    pytester: pytest.Pytester,
) -> None:
    # Same table, same verdicts as in-process, but each scenario's Donkey talks to
    # a real simulator over a socket; the retry is counted from the server's log.
    pytest.importorskip("openai")
    pytest.importorskip("uvicorn")
    pytest.importorskip("starlette")
    pytester.makepyfile(goodagent=_GOOD_AGENT, retryagent=_RETRY_BUG_AGENT)
    good = pytester.runpytest_subprocess(
        "--donkey-conformance", "--donkey-agent=goodagent:build", "--donkey-mode=server", "-v"
    )
    assert good.ret == _EXIT_OK
    good.assert_outcomes(passed=len(_scenario_names()))
    good.stdout.fnmatch_lines(["*Retries a budget refusal*"])
    bad = pytester.runpytest_subprocess(
        "--donkey-conformance", "--donkey-agent=retryagent:build", "--donkey-mode=server", "-v"
    )
    assert bad.ret == _EXIT_TESTS_FAILED
    bad.stdout.fnmatch_lines(["*retries_token_budget*FAIL*"])
    bad.stdout.fnmatch_lines(["*retried*"])


def _scenario_names() -> list[str]:
    # Imported lazily so the module stays import-safe under base-only (the import
    # is framework-free, but keeping it out of module top mirrors the plugin).
    from donkey_kit.conformance.suite import SCENARIOS

    return [s.name for s in SCENARIOS]

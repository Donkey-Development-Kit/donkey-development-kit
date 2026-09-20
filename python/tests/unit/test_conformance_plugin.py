"""End-to-end tests of the shipped pytest plugin (#191, BG §1.5).

These spawn a throwaway pytest session (via the ``pytester`` fixture) that loads
our real ``pytest11`` plugin exactly as a customer's ``pytest --donkey-conformance
--agent=my_app:build`` would, and assert the customer-visible behaviour:

- inert with no ``--donkey-conformance`` flag (the default footprint is just CLI
  options + an unused fixture);
- a clean ``UsageError`` (exit code 4, no traceback) when ``--agent`` is missing
  or a ``KNOWN_LIMITATIONS`` entry is invalid — validated at collection, never a
  silent skip (the conformance kit);
- a green run + printed scenario table for a well-behaved agent, and a red run
  naming the tripped scenario for a broken one.

``runpytest_subprocess`` is used throughout: it runs ``python -m pytest`` in a
fresh process, which both auto-loads the installed entry-point plugin and puts
the tmp dir on ``sys.path`` so ``--agent=<module>:build`` imports the file we
wrote there. The runs that actually build an agent need ``openai`` (the agent
calls ``donkey.openai()``); those tests skip without it. The inert/help and the
two UsageError paths need neither ``openai`` nor a live gateway, so they run
everywhere the base ``[dev]`` install does.
"""

from __future__ import annotations

import pytest

# pytest's own exit codes (pytest.ExitCode); spelled out to keep the asserts legible.
_EXIT_OK = 0
_EXIT_TESTS_FAILED = 1
_EXIT_USAGE_ERROR = 4


# A well-behaved agent module: bridges refusals with classify(), never retries,
# logs the correlation id, tolerates an absent budget. Passes all four scenarios.
_GOOD_AGENT = '''
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
'''

# The headline bug: retries a terminal 429 budget refusal. Fails the retry scenario.
_RETRY_BUG_AGENT = '''
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
'''

# No openai import on purpose: the invalid-exemption path must fail at
# validation, not because the module could not be imported.
_BAD_KNOWN_LIMITATIONS_AGENT = '''
KNOWN_LIMITATIONS = {"not_a_real_scenario": "this key names no scenario"}


def build(donkey):
    return object()
'''


def test_plugin_is_inert_without_the_flag(pytester: pytest.Pytester) -> None:
    # With no --donkey-conformance, normal collection is untouched: an ordinary
    # test in the project is collected and passes as if the plugin weren't there.
    pytester.makepyfile(test_ordinary="def test_ok():\n    assert True\n")
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)


def test_options_are_registered(pytester: pytest.Pytester) -> None:
    # The plugin auto-loads via its entry point, so its options appear in --help
    # even though it stays inert. Proves the plugin is present in the subprocess.
    result = pytester.runpytest_subprocess("--help")
    result.stdout.fnmatch_lines(["*--donkey-conformance*"])
    result.stdout.fnmatch_lines(["*--agent*"])


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


def test_conformance_without_agent_is_a_usage_error(pytester: pytest.Pytester) -> None:
    # --donkey-conformance with no --agent is a misuse: a clean UsageError
    # (exit 4), not a traceback and not a silently-empty run.
    result = pytester.runpytest_subprocess("--donkey-conformance")
    assert result.ret == _EXIT_USAGE_ERROR
    result.stderr.fnmatch_lines(["*--agent*"])


def test_invalid_known_limitations_is_a_usage_error(pytester: pytest.Pytester) -> None:
    # A KNOWN_LIMITATIONS key that names no scenario is rejected at collection
    # time (the conformance kit: asserted, never silent) — a UsageError before any scenario runs.
    pytester.makepyfile(brokenagent=_BAD_KNOWN_LIMITATIONS_AGENT)
    result = pytester.runpytest_subprocess(
        "--donkey-conformance", "--agent=brokenagent:build"
    )
    assert result.ret == _EXIT_USAGE_ERROR
    result.stderr.fnmatch_lines(["*unknown scenario*"])


def test_good_agent_passes_all_scenarios_and_prints_the_table(
    pytester: pytest.Pytester,
) -> None:
    pytest.importorskip("openai")
    pytester.makepyfile(goodagent=_GOOD_AGENT)
    result = pytester.runpytest_subprocess(
        "--donkey-conformance", "--agent=goodagent:build", "-v"
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
        "--donkey-conformance", "--agent=retryagent:build", "-v"
    )
    assert result.ret == _EXIT_TESTS_FAILED
    # The retry scenario item is the one that fails, and its finding is shown.
    result.stdout.fnmatch_lines(["*retries_token_budget*FAIL*"])
    result.stdout.fnmatch_lines(["*retried*"])


def _scenario_names() -> list[str]:
    # Imported lazily so the module stays import-safe under base-only (the import
    # is framework-free, but keeping it out of module top mirrors the plugin).
    from donkey_kit.conformance.suite import SCENARIOS

    return [s.name for s in SCENARIOS]

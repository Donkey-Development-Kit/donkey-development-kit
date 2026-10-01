"""The typed-refusals example's handlers actually run (#791).

``website/content/examples/general/typed-refusals.mdx`` teaches the ``except``
ladder a caller writes against the raw OpenAI client. An earlier version put
the typed handlers beside the ``classify()`` clause of the same ``try``, where
Python never runs them. This test executes the page's own ``def ask(`` fence
against a simulated refusal of each type, and against a dead origin for
``GatewayUnavailable``, so a regression in the published snippet fails CI.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core import errors
from donkey_kit.core.errors import (
    ContentSafetyBlocked,
    DonkeyError,
    PIIDetected,
    PromptInjectionBlocked,
    TokenBudgetExceeded,
    UpstreamModelError,
    UpstreamRequestError,
)

_PAGE = Path(__file__).resolve().parents[3] / "website/content/examples/general/typed-refusals.mdx"

# Simulated refusal -> the start of what its handler in ask() returns.
_HANDLED: list[tuple[type[DonkeyError], str]] = [
    (PIIDetected, "redact"),
    (ContentSafetyBlocked, "revise"),
    (TokenBudgetExceeded, "wait"),
    (PromptInjectionBlocked, "escalate"),
    (UpstreamRequestError, "fix"),
    (UpstreamModelError, "retry"),
]


def _ask() -> Any:
    """Compile the page's ``def ask(`` fence with the names it imports."""
    openai = pytest.importorskip("openai")
    fences = re.findall(r"```python\n(.*?)```", _PAGE.read_text(), re.DOTALL)
    source = next(f for f in fences if f.startswith("def ask("))
    namespace: dict[str, Any] = {"Any": Any, "openai": openai, **vars(errors)}
    exec(compile(source, str(_PAGE), "exec"), namespace)
    return namespace["ask"]


@pytest.fixture
def donkey(monkeypatch: pytest.MonkeyPatch) -> Any:
    # The dead origin must be dialled directly, not through an env proxy.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    cfg = DonkeyConfig(
        llm_proxy_url="http://127.0.0.1:9/",
        llm_proxy_client_id="proxy-client-id",
        llm_proxy_client_secret="proxy-client-secret",
        timeout_s=2.0,
        max_retries=0,
    )
    with Donkey(cfg) as d:
        yield d


@pytest.mark.parametrize(("refusal", "action"), _HANDLED, ids=lambda v: getattr(v, "__name__", v))
def test_each_simulated_refusal_reaches_its_typed_handler(
    donkey: Donkey, refusal: type[DonkeyError], action: str
) -> None:
    ask = _ask()
    client = donkey.openai(sync=True)
    with donkey.simulate(refusal):
        outcome = ask(client, "hello")
    assert outcome.startswith(action), outcome


def test_a_dead_origin_reaches_the_gateway_unavailable_handler(donkey: Donkey) -> None:
    ask = _ask()
    outcome = ask(donkey.openai(sync=True), "hello")
    assert outcome.startswith("checkpoint"), outcome

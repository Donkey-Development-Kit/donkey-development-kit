"""Waiting for the gateway's step-up approval (CIBA), BG §2.3, #214.

``wait_for_approval`` re-sends a call while the OBO policy answers
``authorization_pending`` (#1320), and ``ToolSet.wait_for_gateway_approval()`` applies it
to ``tools/list``. The gateway owns the approval; the loop only waits.
"""

from __future__ import annotations

import asyncio
import warnings
from collections.abc import Mapping
from typing import Any

import pytest

from donkey_kit.core.errors import AuthError, ConfigWarning
from donkey_kit.core.step_up import (
    GatewayApprovalDenied,
    GatewayApprovalPending,
    wait_for_approval,
)
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import session as session_module
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.session import ToolSet


class _Clock:
    """A fake clock whose ``sleep`` advances it, so a wait costs no real time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class _Script:
    """A ``send`` that raises each queued error in turn, then returns ``"ok"``."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


def _pending(after: float | None = 5.0) -> GatewayApprovalPending:
    return GatewayApprovalPending("pending", retry_after=after)


async def test_a_call_that_goes_through_is_sent_once() -> None:
    clock, send = _Clock(), _Script()
    assert await wait_for_approval(send, timeout=60, sleep=clock.sleep, clock=clock) == "ok"
    assert send.calls == 1
    assert clock.slept == []


async def test_pending_is_re_sent_after_the_gateways_retry_after() -> None:
    clock, send = _Clock(), _Script(_pending(5), _pending(8))
    assert await wait_for_approval(send, timeout=60, sleep=clock.sleep, clock=clock) == "ok"
    assert send.calls == 3
    assert clock.slept == [5, 8]


async def test_a_missing_or_extreme_retry_after_is_clamped() -> None:
    clock = _Clock()
    send = _Script(_pending(None), _pending(0), _pending(3600))
    await wait_for_approval(send, timeout=600, sleep=clock.sleep, clock=clock)
    assert clock.slept == [1.0, 1.0, 30.0]


async def test_denied_is_raised_at_once_and_never_re_sent() -> None:
    clock = _Clock()
    send = _Script(_pending(5), GatewayApprovalDenied("no"))
    with pytest.raises(GatewayApprovalDenied):
        await wait_for_approval(send, timeout=60, sleep=clock.sleep, clock=clock)
    assert send.calls == 2


async def test_the_last_pending_error_is_raised_when_the_next_wait_passes_the_timeout() -> None:
    clock = _Clock()
    last = _pending(8)
    send = _Script(_pending(5), last, _pending(8))
    with pytest.raises(GatewayApprovalPending) as info:
        await wait_for_approval(send, timeout=15, sleep=clock.sleep, clock=clock)
    assert info.value is not last  # the third answer: 5 + 8 waited, 8 more would pass 15
    assert info.value.retry_after == 8
    assert send.calls == 3
    assert clock.slept == [5, 8]


async def test_a_zero_timeout_sends_once_like_a_bare_call() -> None:
    clock, send = _Clock(), _Script(_pending(5))
    with pytest.raises(GatewayApprovalPending):
        await wait_for_approval(send, timeout=0, sleep=clock.sleep, clock=clock)
    assert send.calls == 1
    assert clock.slept == []


async def test_other_errors_are_not_retried() -> None:
    clock, send = _Clock(), _Script(AuthError("bad credential"))
    with pytest.raises(AuthError):
        await wait_for_approval(send, timeout=60, sleep=clock.sleep, clock=clock)
    assert send.calls == 1


# ---- ToolSet.wait_for_gateway_approval() ------------------------------------


def _toolset() -> ToolSet:
    return ToolSet(
        [
            McpServerHandle(
                ref=AssetRef("com.acme", "inv", "1.0.0"),
                endpoint_url="https://gw.example.test/inv/",
                governance=GovernanceReport(governed=True, checks=[Check("deployed", True, "ok")]),
            )
        ],
        _auth=ClientIdAuth("cid", "secret"),
    )


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> list[Exception | None]:
    """Queue what each ``tools/list`` call does: an error to raise, or ``None`` to list."""
    queue: list[Exception | None] = []
    sent: list[Mapping[str, str]] = []

    async def fake_list(url: str, headers: Mapping[str, str], *, server: str) -> Any:
        sent.append(headers)
        answer = queue.pop(0) if queue else None
        if answer is not None:
            raise answer
        return ({"name": "add"},)

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(session_module, "list_tools", fake_list)
    monkeypatch.setattr(session_module, "wait_for_approval", _no_sleep(instant))
    return queue


def _no_sleep(instant: Any) -> Any:
    """``wait_for_approval`` with the real loop but no real pause."""
    from donkey_kit.core import step_up

    def run(send: Any, *, timeout: float) -> Any:
        return step_up.wait_for_approval(send, timeout=timeout, sleep=instant)

    return run


async def test_listing_raises_pending_at_once_by_default(
    answers: list[Exception | None],
) -> None:
    answers.extend([_pending(5), None])
    with pytest.raises(GatewayApprovalPending):
        await _toolset().describe()


async def test_listing_waits_for_the_approval_when_asked(
    answers: list[Exception | None],
) -> None:
    answers.extend([_pending(5), _pending(5), None])
    described = await _toolset().wait_for_gateway_approval("2m").describe()
    assert described.servers[0].tool_descriptors == ({"name": "add"},)
    assert answers == []


async def test_listing_raises_denied_without_retrying(
    answers: list[Exception | None],
) -> None:
    answers.extend([GatewayApprovalDenied("no"), None])
    with pytest.raises(GatewayApprovalDenied):
        await _toolset().wait_for_gateway_approval("2m").describe()
    assert len(answers) == 1  # the second answer was never asked for


def test_the_wait_survives_filter_and_require_approval() -> None:
    waiting = _toolset().wait_for_gateway_approval("90s")
    assert waiting._step_up_wait == 90
    assert waiting.filter(allow=["add"])._step_up_wait == 90
    assert waiting.require_approval(tools=["add"])._step_up_wait == 90


def test_the_timeout_must_be_a_positive_duration() -> None:
    with pytest.raises(ValueError, match="positive duration"):
        _toolset().wait_for_gateway_approval(0)


def test_a_wait_past_one_gateway_flow_warns() -> None:
    with pytest.warns(ConfigWarning, match="new approval request"):
        waiting = _toolset().wait_for_gateway_approval("5m")
    assert waiting._step_up_wait == 300


def test_a_wait_within_one_gateway_flow_does_not_warn() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _toolset().wait_for_gateway_approval("2m")
        _toolset().wait_for_gateway_approval(90)


# ---- one server's failure stops the others' waits (#1437) -------------------


def _two_servers() -> ToolSet:
    governed = GovernanceReport(governed=True, checks=[Check("deployed", True, "ok")])
    return ToolSet(
        [
            McpServerHandle(
                ref=AssetRef("com.acme", asset, "1.0.0"),
                endpoint_url=f"https://gw.example.test/{asset}/",
                governance=governed,
            )
            for asset in ("inv", "crm")
        ],
        _auth=ClientIdAuth("cid", "secret"),
    )


class _Servers:
    """Two fake MCP servers whose answers a test scripts per call.

    ``answers[asset]`` is a list with one entry per ``tools/list`` call: an error to raise,
    ``"after crm waits"`` to raise the next entry only once ``crm`` is in its step-up pause,
    or ``None`` to list. A pause blocks until it is cancelled, so a wait nothing cancels
    never ends; ``events`` records the sends and the pauses in order.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, **answers: list[Any]) -> None:
        self.answers = answers
        self.events: list[str] = []
        self.crm_waiting = asyncio.Event()
        self.pausing: dict[str, asyncio.Event] = {}
        monkeypatch.setattr(session_module, "list_tools", self.list_tools)
        monkeypatch.setattr(session_module, "wait_for_approval", self.wait)

    async def list_tools(self, url: str, headers: Mapping[str, str], *, server: str) -> Any:
        asset = server.split("/")[1]
        self.events.append(f"{asset} sends")
        answer = self.answers[asset].pop(0)
        if answer == "after crm waits":
            await self.crm_waiting.wait()
            answer = self.answers[asset].pop(0)
        if answer is not None:
            raise answer
        return ({"name": f"{asset}_tool"},)

    def wait(self, send: Any, *, timeout: float) -> Any:
        from donkey_kit.core import step_up

        async def pause(_seconds: float) -> None:
            asset = send.keywords["server"].split("/")[1]
            self.events.append(f"{asset} waits")
            if asset == "crm":
                self.crm_waiting.set()
            self.pausing.setdefault(asset, asyncio.Event()).set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.events.append(f"{asset} wait cancelled")
                raise

        return step_up.wait_for_approval(send, timeout=timeout, sleep=pause)


async def test_a_denial_on_one_server_cancels_the_others_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servers = _Servers(
        monkeypatch,
        inv=["after crm waits", GatewayApprovalDenied("no")],
        crm=[_pending(5)],
    )
    with pytest.raises(GatewayApprovalDenied):
        await _two_servers().wait_for_gateway_approval("2m").describe()
    assert servers.events == ["inv sends", "crm sends", "crm waits", "crm wait cancelled"]


async def test_an_auth_retry_starts_only_after_the_first_attempts_waits_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servers = _Servers(
        monkeypatch,
        inv=["after crm waits", AuthError("expired"), None],
        crm=[_pending(5), None],
    )
    described = await _two_servers().wait_for_gateway_approval("2m").describe()
    assert [s.tool_descriptors for s in described.servers] == [
        ({"name": "inv_tool"},),
        ({"name": "crm_tool"},),
    ]
    assert servers.events == [
        "inv sends",
        "crm sends",
        "crm waits",
        "crm wait cancelled",
        "inv sends",
        "crm sends",
    ]


async def test_cancelling_describe_cancels_every_servers_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servers = _Servers(monkeypatch, inv=[_pending(5)], crm=[_pending(5)])
    task = asyncio.ensure_future(_two_servers().wait_for_gateway_approval("2m").describe())
    while set(servers.pausing) != {"inv", "crm"}:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(e for e in servers.events if e.endswith("cancelled")) == [
        "crm wait cancelled",
        "inv wait cancelled",
    ]

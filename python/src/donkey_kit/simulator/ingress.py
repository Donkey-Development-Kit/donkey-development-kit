"""``build_ingress`` — the simulated A2A ingress behind ``donkey dev`` (BG §2.9.3, #1284).

Option B of BG §2.9.3: a local fake gateway in front of the app
``donkey.serve()`` runs, replaying the simulator's **shared rejection
fixtures** (the same files ``core.errors.classify()`` and ``donkey mock`` are
tested against) on inbound A2A calls, so a plain A2A client sees the real
rejection bytes and the agent's own bytes otherwise.

A *call* is a JSON-RPC ``SendMessage`` or ``SendStreamingMessage`` POST. The
``--scenario`` rules run on its text parts, and a hit is answered with the
fixture before the agent sees the call. Every other request (the agent card,
``GetTask``, ``CancelTask``, …) passes straight through, and so does a call no
rule rejects; the ``request_limit`` trio rides on a passing call's response.

What it is NOT: no A2A agent instance's rejections have been captured live yet
(#226/#1278), so the shapes are the LLM-proxy ones (docs/verified-apis.md §4),
answered at the HTTP level before the agent sees the call. It enforces no
policy and forwards no traffic off the machine. Every response carries
``x-donkey-simulator: true`` (BG §1.4, non-negotiable).

Framework isolation: a plain ASGI wrapper over the stdlib and the framework-free
fixtures and scenarios, so importing it needs no extra. It wraps any ASGI app
and never imports the ``a2a-sdk``; the ``donkey dev`` command composes the two.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from .app import SIMULATOR_HEADER, ASGIApp, _HonestyStamp
from .fixtures import Fixture, load, replay_headers
from .scenarios import (
    BudgetScenario,
    FaultScenario,
    RequestLimitScenario,
    ScenarioError,
    ScenarioHit,
)

__all__ = ["CALL_METHODS", "build_ingress", "call_text", "check_scenarios"]

_Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
_Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

# The JSON-RPC methods that hand the agent a message, named as the a2a-sdk's
# JSON-RPC dispatcher names them (A2A 1.0). The scenario rules run on these only.
CALL_METHODS = frozenset({"SendMessage", "SendStreamingMessage"})


def check_scenarios(scenarios: tuple[FaultScenario, ...]) -> None:
    """Refuse a rule that has no meaning on an inbound A2A call. ``budget`` is
    the token rate limit, which counts model tokens; an A2A call carries none."""
    for scenario in scenarios:
        if isinstance(scenario, BudgetScenario):
            raise ScenarioError(
                "budget counts model tokens, which an inbound A2A call does not carry; "
                "use request_limit, pii_block or injection"
            )


def call_text(payload: object) -> str | None:
    """The text parts of a JSON-RPC call's message, joined as
    :attr:`~donkey_kit.a2a.A2ATask.input` joins them, or ``None`` when
    ``payload`` is not a call."""
    if not isinstance(payload, dict) or payload.get("method") not in CALL_METHODS:
        return None
    params = payload.get("params")
    message = params.get("message") if isinstance(params, dict) else None
    parts = message.get("parts") if isinstance(message, dict) else None
    if not isinstance(parts, list):
        return ""
    return "\n".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )


class _Ingress:
    """Holds the scenario rules for one ``donkey dev`` run and decides each call."""

    def __init__(self, agent: ASGIApp, scenarios: tuple[FaultScenario, ...]) -> None:
        self._agent = agent
        self._request_limit = next(
            (s for s in scenarios if isinstance(s, RequestLimitScenario)), None
        )
        # Rejection rules in the simulator's precedence: injection before pii_block.
        order = {"injection": 0, "pii_block": 1}
        self._reject = sorted(
            (s for s in scenarios if not isinstance(s, RequestLimitScenario)),
            key=lambda s: order.get(s.name, 99),
        )

    def _decide(self, text: str) -> tuple[ScenarioHit | None, dict[str, str]]:
        """The rejection for this call, if any, and the headers a passing call carries."""
        for scenario in self._reject:
            hit = scenario.on_call(text)
            if hit is not None:
                return hit, {}
        if self._request_limit is None:
            return None, {}
        hit = self._request_limit.on_call(text)
        if hit is not None:
            return hit, {}
        return None, self._request_limit.happy_path_headers()

    async def __call__(
        self, scope: MutableMapping[str, Any], receive: _Receive, send: _Send
    ) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self._agent(scope, receive, send)
            return
        body = await _read_body(receive)
        try:
            payload: object = json.loads(body)
        except ValueError:
            payload = None  # not JSON: the agent's own JSON-RPC parse error answers it
        text = call_text(payload)
        extra: dict[str, str] = {}
        if text is not None:
            hit, extra = self._decide(text)
            if hit is not None:
                await _send_fixture(send, load(hit.shape), hit.extra_headers)
                return
        await self._agent(scope, _replay(body, receive), _with_headers(send, extra))


async def _read_body(receive: _Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break  # the client went away; the agent sees an empty body
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _replay(body: bytes, receive: _Receive) -> _Receive:
    """A ``receive`` that hands the agent the already-read body, then defers to
    the real one (so the agent still sees a disconnect)."""
    sent = False

    async def _receive() -> MutableMapping[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return _receive


def _with_headers(send: _Send, extra: dict[str, str]) -> _Send:
    if not extra:
        return send
    encoded = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in extra.items()]

    async def _send(message: MutableMapping[str, Any]) -> None:
        if message.get("type") == "http.response.start":
            message["headers"] = [*message.get("headers", []), *encoded]
        await send(message)

    return _send


async def _send_fixture(send: _Send, fixture: Fixture, extra: dict[str, str]) -> None:
    """Answer with the fixture's status and bytes, as the simulator replays them."""
    headers = replay_headers(fixture)
    headers.update(extra)
    if fixture.content_type is not None:
        headers["content-type"] = fixture.content_type
    headers["content-length"] = str(len(fixture.body))
    headers[SIMULATOR_HEADER] = "true"
    await send(
        {
            "type": "http.response.start",
            "status": fixture.status,
            "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        }
    )
    await send({"type": "http.response.body", "body": fixture.body})


def build_ingress(agent: ASGIApp, scenarios: tuple[FaultScenario, ...] = ()) -> ASGIApp:
    """Wrap an A2A agent's ASGI app (:func:`donkey_kit.a2a.build_app`) in the
    simulated ingress.

    ``scenarios`` are the parsed ``--scenario`` rules
    (:func:`~donkey_kit.simulator.scenarios.parse_scenarios`); like the
    simulator's, they are stateful and single-use. With none, every request
    passes through and only the honesty header is added.

    Raises:
        ScenarioError: A rule has no meaning on an inbound call (``budget``).
    """
    check_scenarios(scenarios)
    return _HonestyStamp(_Ingress(agent, scenarios))

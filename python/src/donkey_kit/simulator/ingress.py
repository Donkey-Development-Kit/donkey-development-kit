"""``build_ingress`` — the simulated A2A ingress behind ``donkey dev`` (BG §2.9.3, #1284).

Option B of BG §2.9.3: a local fake gateway in front of the app
``donkey.serve()`` runs, replaying **captured A2A rejection fixtures**
(docs/verified-apis.md section 4, #1393) on inbound A2A calls, so a plain A2A
client sees the real rejection bytes and the agent's own bytes otherwise.

A *call* is a JSON-RPC ``SendMessage`` or ``SendStreamingMessage`` POST for
``1.0``, or ``message/send`` / ``message/stream`` for ``0.3.0``. The
``--scenario`` rules run on its text parts, and a hit is answered with the
fixture before the agent sees the call. Every other request (the agent card,
``GetTask``, ``CancelTask``, …) passes straight through, and so does a call no
rule rejects. ``request_limit`` counts calls only to trigger the captured token
rate refusal; it synthesizes no headers, on passing calls or rejections.
A POST body over :data:`MAX_BODY_BYTES` is answered with a ``413`` instead.

What it is NOT: policy evaluation, token counting, outbound A2A simulation or
authorization-code simulation. Send and streaming calls use their matching
request-phase HTTP captures, not SSE refusal events. Response IDs and diagnostic
bytes stay captured evidence, not a promise of ID rewriting or policy identity
(the PII envelope is ambiguous). It enforces no policy and forwards no traffic
off the machine. Every response carries
``x-donkey-simulator: true`` (BG §1.4, non-negotiable).

The v0.3 captures cover PII and rate send refusals only. Injection is refused
at construction; streaming with scenarios and wrong-version calls return local
simulator errors before dispatch, not fabricated gateway/protocol responses.

Framework isolation: a plain ASGI wrapper over the stdlib and the framework-free
fixtures and scenarios, so importing it needs no extra. It wraps any ASGI app
and never imports the ``a2a-sdk``; the ``donkey dev`` command composes the two.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from ..a2a.protocol import matches_protocol_version
from .app import SIMULATOR_HEADER, ASGIApp, _HonestyStamp
from .fixtures import Fixture, load, replay_headers
from .scenarios import (
    BudgetScenario,
    FaultScenario,
    RequestLimitScenario,
    ScenarioError,
)

__all__ = ["CALL_METHODS", "MAX_BODY_BYTES", "build_ingress", "call_text", "check_scenarios"]

_Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
_Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

#: The largest POST body the ingress reads (4 MiB). A larger one is answered
#: with a ``413`` and never reaches the agent, so a non-loopback ``donkey dev``
#: cannot be made to buffer an unbounded body in memory.
MAX_BODY_BYTES = 4 * 1024 * 1024

# The JSON-RPC methods that hand the agent a message, named as the a2a-sdk's
# JSON-RPC dispatcher names them (A2A 1.0). The scenario rules run on these only.
CALL_METHODS = frozenset({"SendMessage", "SendStreamingMessage"})
_METHODS = {
    "1.0": {"SendMessage": "send", "SendStreamingMessage": "stream"},
    "0.3.0": {"message/send": "send", "message/stream": "stream"},
}
_PREFIX = {"1.0": "v1", "0.3.0": "v03"}


def check_scenarios(scenarios: tuple[FaultScenario, ...], *, protocol_version: str = "1.0") -> None:
    """Refuse a rule that has no meaning on an inbound A2A call. ``budget`` is
    the token rate limit, which counts model tokens; an A2A call carries none."""
    if protocol_version not in _METHODS:
        raise ScenarioError(
            f"unsupported A2A protocol version {protocol_version!r}; use 0.3.0 or 1.0"
        )
    supported = {"pii_block", "request_limit"}
    if protocol_version == "1.0":
        supported.add("injection")
    for scenario in scenarios:
        if isinstance(scenario, BudgetScenario):
            raise ScenarioError(
                "budget counts model tokens, which an inbound A2A call does not carry; "
                f"use {', '.join(sorted(supported))}"
            )
        if scenario.name not in supported:
            raise ScenarioError(
                f"{scenario.name} has no matching captured A2A {_PREFIX[protocol_version]} "
                f"ingress refusal (protocol {protocol_version}); use {', '.join(sorted(supported))}"
            )


def call_text(payload: object, *, protocol_version: str = "1.0") -> str | None:
    """The text parts of a JSON-RPC call's message, joined as
    :attr:`~donkey_kit.a2a.A2ATask.input` joins them, or ``None`` when
    ``payload`` is not a call."""
    check_scenarios((), protocol_version=protocol_version)
    if not isinstance(payload, dict) or payload.get("method") not in _METHODS[protocol_version]:
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

    def __init__(
        self, agent: ASGIApp, scenarios: tuple[FaultScenario, ...], protocol_version: str
    ) -> None:
        self._agent = agent
        self._protocol_version = protocol_version
        self._scenarios = scenarios
        self._request_limit = next(
            (s for s in scenarios if isinstance(s, RequestLimitScenario)), None
        )
        # Rejection rules in the simulator's precedence: injection before pii_block.
        order = {"injection": 0, "pii_block": 1}
        self._reject = sorted(
            (s for s in scenarios if not isinstance(s, RequestLimitScenario)),
            key=lambda s: order.get(s.name, 99),
        )

    def _decide(self, text: str) -> str | None:
        """Select A2A evidence using the shared rules, not their LLM shape or overrides."""
        for scenario in self._reject:
            if scenario.on_call(text) is not None:
                return {"injection": "azure-block", "pii_block": "pii"}[scenario.name]
        if self._request_limit is not None and self._request_limit.on_call(text) is not None:
            return "rate"
        return None

    async def __call__(
        self, scope: MutableMapping[str, Any], receive: _Receive, send: _Send
    ) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self._agent(scope, receive, send)
            return
        body = await _read_body(scope, receive)
        if body is None:
            await _send_too_large(send)
            return
        try:
            payload: object = json.loads(body)
        except ValueError:
            payload = None  # not JSON: the agent's own JSON-RPC parse error answers it
        method = payload.get("method") if isinstance(payload, dict) else None
        if isinstance(method, str) and any(method in methods for methods in _METHODS.values()):
            if method not in _METHODS[self._protocol_version]:
                await _send_unsupported(
                    send, f"{method} is not an A2A {self._protocol_version} call method"
                )
                return
            versions = [
                value.decode("latin-1")
                for key, value in scope.get("headers", [])
                if key.lower() == b"a2a-version"
            ]
            if any(
                not matches_protocol_version(version, self._protocol_version)
                for version in versions
            ):
                await _send_unsupported(
                    send, f"A2A-Version does not match selected protocol {self._protocol_version}"
                )
                return
            if self._protocol_version == "0.3.0" and method == "message/stream" and self._scenarios:
                await _send_unsupported(
                    send,
                    "message/stream has no matching captured A2A v03 ingress refusal "
                    "for pii_block or request_limit; use message/send or remove the scenarios",
                )
                return
        text = call_text(payload, protocol_version=self._protocol_version)
        if text is not None:
            policy = self._decide(text)
            if policy is not None:
                assert isinstance(payload, dict)  # call_text only accepts JSON-RPC call objects.
                suffix = _METHODS[self._protocol_version][payload["method"]]
                await _send_fixture(
                    send, load(f"a2a-{_PREFIX[self._protocol_version]}-{policy}-{suffix}")
                )
                return
        await self._agent(scope, _replay(body, receive), send)


def _declared_length(scope: MutableMapping[str, Any]) -> int | None:
    for key, value in scope.get("headers", []):
        if key.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _read_body(scope: MutableMapping[str, Any], receive: _Receive) -> bytes | None:
    """The request body, or ``None`` once it is over :data:`MAX_BODY_BYTES`. A
    declared ``content-length`` over the cap is refused before any byte is read."""
    declared = _declared_length(scope)
    if declared is not None and declared > MAX_BODY_BYTES:
        return None
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break  # the client went away; the agent sees an empty body
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


async def _send_too_large(send: _Send) -> None:
    body = f"request body over the donkey dev ingress limit of {MAX_BODY_BYTES} bytes\n".encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("latin-1")),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_unsupported(send: _Send, detail: str) -> None:
    """A local simulator configuration error, not an invented gateway refusal."""
    body = f"donkey dev ingress: {detail}\n".encode()
    await send(
        {
            "type": "http.response.start",
            "status": 400,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


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


async def _send_fixture(send: _Send, fixture: Fixture) -> None:
    """Answer with the fixture's status and bytes, as the simulator replays them."""
    headers = replay_headers(fixture)
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


def build_ingress(
    agent: ASGIApp,
    scenarios: tuple[FaultScenario, ...] = (),
    *,
    protocol_version: str = "1.0",
) -> ASGIApp:
    """Wrap an A2A agent's ASGI app (:func:`donkey_kit.a2a.build_app`) in the
    simulated ingress.

    ``scenarios`` are the parsed ``--scenario`` rules
    (:func:`~donkey_kit.simulator.scenarios.parse_scenarios`); like the
    simulator's, they are stateful and single-use. With none, every request
    passes through and only the honesty header is added, except wrong-version
    calls. ``protocol_version`` selects ``1.0`` (default) or ``0.3.0``. The
    latter has PII/rate send captures only; with scenarios, streaming returns
    a local HTTP 400 before any rule advances, even when no rule would reject.
    Explicit mismatched version headers also return a local HTTP 400. Missing
    headers retain the existing replay behavior; passing calls are validated
    by the official SDK.

    Raises:
        ScenarioError: A rule has no meaning on an inbound call (``budget``),
            no matching captured refusal, or an unsupported protocol version.
    """
    check_scenarios(scenarios, protocol_version=protocol_version)
    return _HonestyStamp(_Ingress(agent, scenarios, protocol_version))

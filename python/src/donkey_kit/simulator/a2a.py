"""Offline outbound A2A capture injection for ``donkey.simulate_a2a()`` (BG §1.4 / §1.5, #1652).

Declared agents stand in for the registry inside the block (ADR 0025). Their
endpoints live on the reserved ``.invalid`` host, and one transport fronts the
shared async client: it answers that host with the packaged Omni Gateway
capture (#1393) for the next ``times`` matching calls, and an offline agent
card and reply otherwise. Every other host is delegated unchanged. httpx only;
the official a2a-sdk is used by ``donkey.a2a`` itself, never here.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Any, cast

import httpx

from .._testing import a2a_discovery_scope, http_clients, swap_transport, transport_of
from ..registry.models import AgentHandle, AssetRef
from ..tools.auth import ClientIdAuth
from .app import SIMULATOR_HEADER
from .fixtures import Fixture, load, replay_headers

if TYPE_CHECKING:
    from ..donkey import Donkey

__all__ = ["CAPTURES", "simulate_declared"]

#: Where a declared agent "lives": the reserved ``.invalid`` TLD never resolves (RFC 2606).
_DECLARED_HOST = "simulated.donkey-kit.invalid"

#: The JSON-RPC methods a ``*-send`` / ``*-stream`` capture answers, per protocol version.
_METHODS = {
    ("1.0", "send"): "SendMessage",
    ("1.0", "stream"): "SendStreamingMessage",
    ("0.3", "send"): "message/send",
    ("0.3", "stream"): "message/stream",
}

#: ``(name, version)`` -> packaged capture shape. Exactly the #1393 captures:
#: v0.3 has no stream or Azure block capture, so those pairs are refused.
CAPTURES: dict[tuple[str, str], str] = {
    **{
        (f"{policy}-{method}", "1.0"): f"a2a-v1-{policy}-{method}"
        for policy in ("pii", "azure-block", "rate")
        for method in ("send", "stream")
    },
    ("pii-send", "0.3"): "a2a-v03-pii-send",
    ("rate-send", "0.3"): "a2a-v03-rate-send",
}


def _default_reply(_text: str) -> str:
    return "simulated reply"


class _A2AReplay(httpx.AsyncBaseTransport):
    """Answers the declared host; delegates every other request to ``inner``."""

    def __init__(
        self,
        inner: Any,  # noqa: ANN401 - the previous inner transport, of any kind
        fixture: Fixture,
        method: str,
        version: str,
        times: int,
        reply: Callable[[str], str],
    ) -> None:
        self._inner = inner
        self._fixture = fixture
        self._method = method
        self._version = version
        self._remaining = times
        self._reply = reply

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != _DECLARED_HOST:
            return cast(httpx.Response, await self._inner.handle_async_request(request))
        headers = {SIMULATOR_HEADER: "true"}
        endpoint = str(request.url.copy_with(path=request.url.path.split(".well-known")[0]))
        if request.method == "GET":
            return httpx.Response(200, headers=headers, json=self._card(endpoint), request=request)
        rpc = json.loads(request.content)
        if rpc.get("method") == self._method and self._remaining > 0:
            self._remaining -= 1
            return self._refusal(request)
        return self._success(request, rpc, headers)

    def _card(self, endpoint: str) -> dict[str, object]:
        base: dict[str, object] = {
            "name": "simulated agent",
            "description": "Declared in donkey.simulate_a2a()",
            "version": "1",
            "skills": [],
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "capabilities": {"streaming": True},
        }
        if self._version == "0.3":
            return {
                **base,
                "url": endpoint,
                "protocolVersion": "0.3.0",
                "preferredTransport": "JSONRPC",
            }
        interface = {"url": endpoint, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        return {**base, "supportedInterfaces": [interface]}

    def _refusal(self, request: httpx.Request) -> httpx.Response:
        headers = replay_headers(self._fixture)
        if self._fixture.content_type is not None:
            headers["content-type"] = self._fixture.content_type
        headers[SIMULATOR_HEADER] = "true"
        return httpx.Response(
            self._fixture.status, headers=headers, content=self._fixture.body, request=request
        )

    def _success(
        self, request: httpx.Request, rpc: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        message = (rpc.get("params") or {}).get("message") or {}
        text = "".join(
            part["text"]
            for part in message.get("parts") or ()
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        answer = self._reply(text)
        legacy = self._version == "0.3"
        reply = {
            "messageId": "simulated-reply",
            "role": "agent" if legacy else "ROLE_AGENT",
            "parts": [{"kind": "text", "text": answer}] if legacy else [{"text": answer}],
        }
        result = {**reply, "kind": "message"} if legacy else {"message": reply}
        payload = {"jsonrpc": "2.0", "id": rpc.get("id"), "result": result}
        if rpc.get("method") in {_METHODS[("1.0", "stream")], _METHODS[("0.3", "stream")]}:
            return httpx.Response(
                200,
                headers={**headers, "content-type": "text/event-stream"},
                content=f"data: {json.dumps(payload)}\n\n",
                request=request,
            )
        return httpx.Response(200, headers=headers, json=payload, request=request)


def _declared(agents: Sequence[str | AssetRef]) -> list[AgentHandle]:
    if isinstance(agents, (str, AssetRef)) or not isinstance(agents, Sequence) or not agents:
        raise ValueError(
            "simulate_a2a() needs agents=[...]: a non-empty list of group_id/asset_id/version refs"
        )
    handles = []
    for agent in agents:
        ref = AssetRef.parse(agent)
        url = f"https://{_DECLARED_HOST}/{ref.group_id}/{ref.asset_id}/{ref.version}/"
        handles.append(AgentHandle(ref, url))
    return handles


@contextlib.contextmanager
def simulate_declared(
    donkey: Donkey,
    capture: str,
    agents: Sequence[str | AssetRef],
    *,
    version: str = "1.0",
    times: int = 1,
    reply: Callable[[str], str] | None = None,
) -> Iterator[None]:
    """See :meth:`donkey_kit.Donkey.simulate_a2a`."""
    if version not in {"1.0", "0.3"}:
        raise ValueError(f"version must be '1.0' or '0.3', got {version!r}")
    shape = CAPTURES.get((capture, version))
    if shape is None:
        names = sorted({name for name, v in CAPTURES if v == version})
        raise ValueError(
            f"No packaged A2A v{version} capture is named {capture!r}. "
            f"Choose one of: {', '.join(names)}."
        )
    if isinstance(times, bool) or not isinstance(times, int) or times < 1:
        raise ValueError(f"times must be an int >= 1, got {times!r}")
    if reply is not None and not callable(reply):
        raise ValueError("reply must be a callable taking the request text and returning a str")
    handles = _declared(agents)
    method = _METHODS[(version, capture.rsplit("-", 1)[1])]
    client = http_clients(donkey)[0]
    with a2a_discovery_scope(donkey, handles, auth=ClientIdAuth("simulated", "simulated")):
        previous = transport_of(client)
        swap_transport(
            client,
            _A2AReplay(previous, load(shape), method, version, times, reply or _default_reply),
        )
        try:
            yield
        finally:
            swap_transport(client, previous)

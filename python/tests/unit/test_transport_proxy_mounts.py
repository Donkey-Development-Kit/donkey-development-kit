"""The transport-swap seam stays offline behind an HTTP proxy (#801).

httpx mounts proxy transports from ``HTTP(S)_PROXY`` / ``ALL_PROXY`` and checks
``client._mounts`` before ``client._transport``, so a swap that replaced only
``_transport`` sent ``simulate()`` and conformance-probe traffic out through the
proxy. The Donkey clients fold their mounts into the base transport at
construction; these tests pin the three halves of that contract:

  1. under any proxy variable, a swapped-in fixture serves every request and no
     socket is opened (``socket.connect`` is blocked, so an escape fails loudly);
  2. real traffic — including ``simulate()`` pass-through after ``times`` — still
     goes through the configured proxy;
  3. a swap refuses (fails closed) if a mount appears after construction.
"""

from __future__ import annotations

import socket
import socketserver
import threading
from collections.abc import Iterator

import httpx
import pytest
from httpx._utils import URLPattern

from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import PIIDetected, classify
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient
from donkey_kit.core.transport.governed import _SyncMountRouter

_URL = "http://sim.local/responses"
_DEAD_PROXY = "http://127.0.0.1:9"
_PROXY_VARS = ["HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"]


@pytest.fixture
def no_sockets(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Fail any outbound connection while a fixture transport is swapped in.
    Both the sync (``socket.create_connection``) and async (asyncio
    ``sock_connect``) paths end in ``socket.connect``/``connect_ex``."""
    attempts: list[object] = []

    def refuse(self: socket.socket, address: object) -> None:
        attempts.append(address)
        raise AssertionError(f"offline seam opened a socket to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return attempts


@pytest.fixture(params=_PROXY_VARS)
def dead_proxy(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    for name in (*_PROXY_VARS, "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv(request.param, _DEAD_PROXY)
    # The request URL whose scheme this variable proxies, so every case is one
    # the unfixed seam really leaked (HTTPS_PROXY never applies to http://).
    return _URL.replace("http://", "https://") if request.param == "HTTPS_PROXY" else _URL


# --- 1. offline under a proxy ------------------------------------------------


def test_env_proxy_is_folded_into_the_base_transport(dead_proxy: str) -> None:
    # The precondition the swap relies on: nothing left in _mounts to win over
    # _transport, on either client.
    cfg = DonkeyConfig()
    with DonkeyClient(cfg) as sync:
        assert sync._mounts == {}
    assert DonkeyAsyncClient(cfg, None)._mounts == {}


async def test_simulate_stays_offline_under_proxy(
    dead_proxy: str, no_sockets: list[object]
) -> None:
    donkey = Donkey(DonkeyConfig())
    async with donkey:
        with donkey.simulate(PIIDetected):
            resp = await donkey._http.post(dead_proxy, json={"model": "gpt-5.1"})
    assert isinstance(classify(resp), PIIDetected)
    assert no_sockets == []


def test_sync_simulate_stays_offline_under_proxy(
    dead_proxy: str, no_sockets: list[object]
) -> None:
    donkey = Donkey(DonkeyConfig())
    sync = donkey._sync_http_client()
    with donkey.simulate(PIIDetected):
        resp = sync.post(dead_proxy, json={"model": "gpt-5.1"})
    assert isinstance(classify(resp), PIIDetected)
    assert no_sockets == []


async def test_conformance_harness_stays_offline_under_proxy(
    dead_proxy: str, no_sockets: list[object]
) -> None:
    openai = pytest.importorskip("openai")
    from donkey_kit.conformance import run_conformance
    from donkey_kit.conformance.suite import SCENARIOS
    from donkey_kit.core.telemetry import current_correlation_id

    class Agent:
        def __init__(self, donkey: Donkey) -> None:
            self._client = donkey.openai()

        async def run(self, prompt: str) -> str:
            import logging

            try:
                resp = await self._client.responses.create(model="gpt-5.1", input=prompt)
            except openai.APIStatusError as exc:
                raise classify(exc.response) from exc
            logging.getLogger(__name__).info("correlation_id=%s", current_correlation_id())
            return str(resp.output_text)

    results = await run_conformance(Agent)
    assert {r.scenario: r.status for r in results} == {s.name: "pass" for s in SCENARIOS}
    assert no_sockets == []


# --- 2. real traffic still honours the proxy ---------------------------------


class _ProxyHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.server.request_lines.append(self.rfile.readline().decode().strip())  # type: ignore[attr-defined]
        while self.rfile.readline() not in (b"\r\n", b""):
            pass
        self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")


@pytest.fixture
def live_proxy(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """A local forward proxy that records each request line it receives."""
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _ProxyHandler)
    server.daemon_threads = True
    server.request_lines = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for name in (*_PROXY_VARS, "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{server.server_address[1]}")
    try:
        yield server.request_lines  # type: ignore[attr-defined]
    finally:
        server.shutdown()
        server.server_close()


def test_real_sync_traffic_goes_through_the_proxy(live_proxy: list[str]) -> None:
    with DonkeyClient(DonkeyConfig()) as client:
        resp = client.get("http://gateway.invalid/ping")
    assert resp.text == "ok"
    assert live_proxy == ["GET http://gateway.invalid/ping HTTP/1.1"]


async def test_simulate_pass_through_goes_through_the_proxy(live_proxy: list[str]) -> None:
    # After ``times`` the fixture delegates to the previous transport — the
    # folded router — so the passed-through call still honours HTTP_PROXY.
    donkey = Donkey(DonkeyConfig())
    async with donkey:
        with donkey.simulate(PIIDetected, times=1):
            refused = await donkey._http.post(_URL, json={"model": "gpt-5.1"})
            passed = await donkey._http.post(_URL, json={"model": "gpt-5.1"})
    assert isinstance(classify(refused), PIIDetected)
    assert passed.text == "ok"
    assert live_proxy == [f"POST {_URL} HTTP/1.1"]


def test_router_honours_no_proxy_entries_and_first_match() -> None:
    # A ``None`` mount (how httpx encodes a NO_PROXY host) routes to the default.
    default = httpx.MockTransport(lambda r: httpx.Response(200, text="direct"))
    proxy = httpx.MockTransport(lambda r: httpx.Response(200, text="proxy"))
    router = _SyncMountRouter(
        default, [(URLPattern("all://sim.local"), None), (URLPattern("all://"), proxy)]
    )
    with httpx.Client(transport=router) as client:
        assert client.get("http://sim.local/").text == "direct"
        assert client.get("http://elsewhere/").text == "proxy"


def test_explicit_mounts_are_folded_honoured_and_closed() -> None:
    closed: list[str] = []

    class Recording(httpx.MockTransport):
        def close(self) -> None:
            closed.append("mount")

    mount = Recording(lambda r: httpx.Response(200, text="mounted"))
    client = DonkeyClient(DonkeyConfig(), mounts={"http://": mount})
    assert client._mounts == {}
    assert client.get("http://anywhere/").text == "mounted"
    client.close()
    assert closed == ["mount"]


# --- 3. fail closed ----------------------------------------------------------


@pytest.mark.parametrize("make", [lambda: DonkeyClient(DonkeyConfig()),
                                  lambda: DonkeyAsyncClient(DonkeyConfig(), None)])
def test_swap_refuses_when_a_mount_could_bypass_it(make: object) -> None:
    client = make()  # type: ignore[operator]
    client._mounts = {URLPattern("all://"): httpx.MockTransport(lambda r: httpx.Response(200))}
    with pytest.raises(RuntimeError, match="mounts that would bypass it"):
        client.governed_transport.replace_inner(httpx.MockTransport(lambda r: httpx.Response(200)))

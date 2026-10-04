"""One shared async client, many event loops (#807).

A sync app wraps each agent call in ``asyncio.run()`` (a Flask or Django view, a
CLI, a per-test pytest-asyncio loop), so every call runs on a new loop. httpx
keeps keep-alive connections in a pool tied to the loop that opened them, so
before the fix the second call on one ``Donkey`` reused a dead loop's connection
and raised a raw ``RuntimeError: Event loop is closed``.

``httpx.MockTransport`` holds no connections, so it cannot reproduce this. These
tests use a real keep-alive HTTP/1.1 server on loopback.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import PIIDetected
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.core.transport.governed import _AsyncMountRouter, _LoopLocalTransport
from donkey_kit.integrations._base import Adapter, default_adapter

pytestmark = pytest.mark.filterwarnings("ignore::donkey_kit.core._verify.UnverifiedValueWarning")


class _KeepAlive(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep the connection open between requests

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("content-length", 0)))
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _KeepAlive)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()


def _cfg(url: str) -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url=url, llm_proxy_client_id="id", llm_proxy_client_secret="s", max_retries=0
    )


async def _post(client: httpx.AsyncClient, url: str) -> int:
    response = await client.post(url + "chat/completions", json={"model": "m", "messages": []})
    return response.status_code


def test_three_asyncio_run_calls_on_one_donkey(url: str) -> None:
    donkey = Donkey(_cfg(url))
    assert [asyncio.run(_post(donkey._http, url)) for _ in range(3)] == [200, 200, 200]
    # Closing on yet another loop must not touch the dead loops' sockets.
    asyncio.run(donkey.aclose())


def test_one_asyncio_run_per_request_does_not_pile_up_pools(url: str) -> None:
    """A closed loop's pool is dropped when the next loop arrives, so a sync
    server calling ``asyncio.run`` per request keeps one pool, not one per
    request."""
    client = DonkeyAsyncClient(_cfg(url), None)
    for _ in range(5):
        assert asyncio.run(_post(client, url)) == 200
    assert isinstance(client.governed_transport.inner, _LoopLocalTransport)
    assert len(client.governed_transport.inner._pools) == 1


class _Plain(Adapter):
    def connection_kwargs(self) -> dict[str, Any]:
        return {}


def test_three_asyncio_run_calls_on_the_module_level_factories(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The module-level factories share one process-wide client
    (``default_adapter``), so they hit the same bug."""
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", url)
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "id")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "s")
    results = [asyncio.run(_post(default_adapter(_Plain)._http, url)) for _ in range(3)]
    assert results == [200, 200, 200]


def test_simulate_passes_through_to_the_running_loops_pool(url: str) -> None:
    """``simulate()`` wraps the client's transport and delegates once its
    countdown is spent. On a later loop that delegation must reach that loop's
    pool, not the first loop's."""
    donkey = Donkey(_cfg(url))
    assert asyncio.run(_post(donkey._http, url)) == 200

    async def simulated() -> list[int]:
        with donkey.simulate(PIIDetected, times=1):
            return [await _post(donkey._http, url), await _post(donkey._http, url)]

    assert asyncio.run(simulated()) == [403, 200]


def test_concurrent_loops_in_threads_get_separate_pools(url: str) -> None:
    """A threaded server running ``asyncio.run`` per request has several live
    loops at once, so a single shared pool cannot work."""
    donkey = Donkey(_cfg(url))
    a_called, b_done = threading.Event(), threading.Event()
    results: dict[str, object] = {}

    async def loop_a() -> list[int]:
        # Leaves an idle keep-alive connection owned by this still-running loop.
        first = await _post(donkey._http, url)
        a_called.set()
        await asyncio.to_thread(b_done.wait, 10)
        return [first, await _post(donkey._http, url)]

    async def loop_b() -> list[int]:
        return [await asyncio.wait_for(_post(donkey._http, url), 5) for _ in range(2)]

    def run(name: str, main: Any, after: threading.Event | None = None) -> None:
        if after is not None:
            after.wait(10)
        try:
            results[name] = asyncio.run(main())
        except BaseException as exc:  # noqa: BLE001 - any outcome is recorded, then asserted below
            results[name] = repr(exc)
        finally:
            if name == "b":
                b_done.set()

    threads = [
        threading.Thread(target=run, args=("a", loop_a)),
        threading.Thread(target=run, args=("b", loop_b, a_called)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert results == {"a": [200, 200], "b": [200, 200]}


def test_env_proxy_mounts_also_get_a_pool_per_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    client = DonkeyAsyncClient(DonkeyConfig(), None)
    # The proxy mounts are folded into one router (#801); each route it
    # dispatches to is a per-loop pool.
    router = client.governed_transport.inner
    assert isinstance(router, _AsyncMountRouter)
    assert router._routes
    assert all(isinstance(t, _LoopLocalTransport) for t in router._children())


def test_a_caller_supplied_transport_is_left_as_given() -> None:
    mock = httpx.MockTransport(lambda request: httpx.Response(200))
    client = DonkeyAsyncClient(DonkeyConfig(), None, transport=mock)
    assert client.governed_transport.inner is mock

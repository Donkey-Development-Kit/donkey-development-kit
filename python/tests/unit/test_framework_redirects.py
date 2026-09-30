"""A proxy redirect to another origin never forwards the proxy credentials.

Two real loopback servers: the proxy answers every request with a ``307`` to
a second origin, which records the headers it receives. Each factory's native
client is built as a developer would build it and called once; the second
origin must receive no credential header — no ``client_id``/``client_secret``
pair, no ``Authorization``, no ``x-api-key``, no wallet ``X-Client-Id``.

The SDK hands every framework that accepts an HTTP client its own shared client,
which does not follow redirects and drops credentials for any origin it was not
checked for; CrewAI gets an interceptor that does the same on its transport.
Each test skips unless its framework's extra is installed.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Awaitable, Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from donkey_kit import Donkey, DonkeyConfig

_CREDENTIAL_NAMES = ("client_id", "client_secret", "authorization", "x-api-key", "x-client-id")
_SECRET = "proxy-client-secret"
_KEY = "proxy-key-value"


class _Servers:
    def __init__(self) -> None:
        self.proxy_headers: list[dict[str, str]] = []
        self.other_headers: list[dict[str, str]] = []
        self._servers: list[ThreadingHTTPServer] = []

    def start(self) -> None:
        servers = self

        class Other(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                servers.other_headers.append({k.lower(): v for k, v in self.headers.items()})
                length = int(self.headers.get("content-length") or 0)
                self.rfile.read(length)
                body = b'{"error": {"message": "not here", "type": "invalid_request_error"}}'
                self.send_response(400)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _answer

            def log_message(self, *args: object) -> None:
                pass

        other = ThreadingHTTPServer(("127.0.0.1", 0), Other)
        other_origin = f"http://127.0.0.1:{other.server_address[1]}"

        class Proxy(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                servers.proxy_headers.append({k.lower(): v for k, v in self.headers.items()})
                length = int(self.headers.get("content-length") or 0)
                self.rfile.read(length)
                self.send_response(307)
                self.send_header("location", f"{other_origin}{self.path}")
                self.send_header("content-length", "0")
                self.end_headers()

            do_GET = do_POST = _answer

            def log_message(self, *args: object) -> None:
                pass

        proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        self.proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}/"
        self._servers = [proxy, other]
        for server in self._servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        for server in self._servers:
            server.shutdown()
            server.server_close()

    def leaked(self) -> list[dict[str, str]]:
        return [
            {k: v for k, v in headers.items() if k in _CREDENTIAL_NAMES}
            for headers in self.other_headers
            if any(k in _CREDENTIAL_NAMES for k in headers)
        ]


@pytest.fixture
def servers() -> Iterator[_Servers]:
    s = _Servers()
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _donkey(servers: _Servers) -> Donkey:
    return Donkey(
        DonkeyConfig(
            llm_proxy_url=servers.proxy_url,
            llm_proxy_client_id="proxy-client-id",
            llm_proxy_client_secret=_SECRET,
            llm_proxy_key=_KEY,
            max_retries=0,
        )
    )


async def _call(servers: _Servers, build: Callable[[Donkey], Awaitable[Any] | Any]) -> None:
    donkey = _donkey(servers)
    try:
        with contextlib.suppress(Exception):
            result = build(donkey)
            if hasattr(result, "__await__"):
                await result
    finally:
        await donkey.aclose()
    assert servers.proxy_headers, "the call never reached the proxy"
    assert servers.proxy_headers[0].get("client_secret") == _SECRET
    assert servers.leaked() == []


_MESSAGES = [{"role": "user", "content": "hi"}]


async def test_raw_async_client(servers: _Servers) -> None:
    pytest.importorskip("openai")
    await _call(
        servers,
        lambda d: d.llm.client().chat.completions.create(model="m", messages=_MESSAGES),
    )


async def test_raw_sync_client(servers: _Servers) -> None:
    pytest.importorskip("openai")
    await _call(
        servers,
        lambda d: d.llm.client(sync=True).chat.completions.create(model="m", messages=_MESSAGES),
    )


async def test_langgraph_async(servers: _Servers) -> None:
    pytest.importorskip("langchain_openai")
    await _call(servers, lambda d: d.langgraph.chat_model("m").ainvoke("hi"))


async def test_langgraph_sync(servers: _Servers) -> None:
    pytest.importorskip("langchain_openai")
    await _call(servers, lambda d: d.langgraph.chat_model("m").invoke("hi"))


async def test_llamaindex_sync(servers: _Servers) -> None:
    pytest.importorskip("llama_index.llms.openai_like")
    await _call(servers, lambda d: d.llamaindex.llm("m").complete("hi"))


async def test_llamaindex_async(servers: _Servers) -> None:
    pytest.importorskip("llama_index.llms.openai_like")
    await _call(servers, lambda d: d.llamaindex.llm("m").acomplete("hi"))


async def test_agent_framework(servers: _Servers) -> None:
    pytest.importorskip("agent_framework.openai")
    await _call(servers, lambda d: d.agent_framework.chat_client("m").get_response("hi"))


async def test_crewai(servers: _Servers) -> None:
    pytest.importorskip("crewai")
    await _call(servers, lambda d: d.crewai.llm("m").call("hi"))


async def test_crewai_async(servers: _Servers) -> None:
    pytest.importorskip("crewai")
    await _call(servers, lambda d: d.crewai.llm("m").acall("hi"))


async def _adk_generate(model: Any) -> None:
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    request = LlmRequest(
        model=model.model,
        contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
    )
    async for _ in model.generate_content_async(request):
        pass


async def test_adk_litellm(servers: _Servers) -> None:
    pytest.importorskip("google.adk.models.lite_llm")
    await _call(servers, lambda d: _adk_generate(d.adk.model("m")))


async def test_adk_gemini(servers: _Servers) -> None:
    pytest.importorskip("google.adk.models")
    await _call(servers, lambda d: _adk_generate(d.adk.gemini("m")))


async def _strands_stream(model: Any) -> None:
    async for _ in model.stream([{"role": "user", "content": [{"text": "hi"}]}]):
        pass


async def test_strands(servers: _Servers) -> None:
    pytest.importorskip("strands.models.openai")
    await _call(servers, lambda d: _strands_stream(d.strands.model("m")))


async def test_openai_agents(servers: _Servers) -> None:
    pytest.importorskip("agents")
    await _call(
        servers,
        lambda d: d.openai_agents.connection_kwargs()["openai_client"].chat.completions.create(
            model="m", messages=_MESSAGES
        ),
    )


async def test_anthropic(servers: _Servers) -> None:
    pytest.importorskip("anthropic")
    await _call(
        servers,
        lambda d: d.anthropic.client().messages.create(
            model="m", max_tokens=1, messages=[{"role": "user", "content": "hi"}]
        ),
    )

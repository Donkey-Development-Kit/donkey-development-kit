"""A policy refusal reaches the proxy exactly once, whichever framework sends it (#734).

The transport owns retries (BG §1.1): it retries 502/503/504 up to
``max_retries`` times and never retries a 4xx, because on this proxy a 429 is a
token-budget refusal (BG §1.2, the conformance kit's ``retries_token_budget``).
A framework or provider SDK that keeps its own retry loop on top re-sends a
refusal against a budget that is already spent, and multiplies the load on a
degraded gateway.

One real loopback proxy counts the requests it receives. Each factory's native
object is built as a developer would build it and called once:

* against the captured ``TokenBudgetExceeded`` 429 it must send exactly once;
* against a persistent 503 it must send ``max_retries + 1`` times when its
  calls go through the shared transport, and once when the framework builds its
  own client (CrewAI), whose retries are turned off.

A framework that cannot be held to one send records an ASSERTED exemption on
its case: the exact send count it makes and why, so a change upstream fails
here rather than passing silently. Each case skips unless its framework's extra
is installed; the meta-tests at the bottom keep the table in step with
``ADAPTERS``.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.integrations import ADAPTERS
from donkey_kit.simulator.fixtures import parse_headers

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "llm_proxy"
_BUDGET_HEADERS = {
    name: value
    for name, value in parse_headers(
        (_FIXTURES / "reject.token-rate-limit.headers.txt").read_text()
    ).items()
    if name.lower() not in {"content-length", "connection"}
}
_MAX_RETRIES = 3


class _Proxy:
    """A loopback proxy that answers every request with one fixed refusal."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.sends = 0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                with proxy._lock:
                    proxy.sends += 1
                length = int(self.headers.get("content-length") or 0)
                self.rfile.read(length)
                self.send_response(proxy.status)
                if proxy.status == 429:
                    # The live capture: an empty body, the budget state in headers.
                    for name, value in _BUDGET_HEADERS.items():
                        self.send_header(name, value)
                    body = b""
                else:
                    # Retry-After: 0 keeps the transport's own backoff out of the test.
                    self.send_header("retry-after", "0")
                    self.send_header("content-type", "application/json")
                    body = b'{"error": {"message": "upstream unavailable"}}'
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _answer

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture(params=[429, 503], ids=["budget-429", "unavailable-503"])
def proxy(request: pytest.FixtureRequest) -> Iterator[_Proxy]:
    p = _Proxy(request.param)
    p.start()
    try:
        yield p
    finally:
        p.stop()


_MESSAGES = [{"role": "user", "content": "hi"}]


async def _adk_generate(model: Any) -> None:
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    request = LlmRequest(
        model=model.model,
        contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
    )
    async for _ in model.generate_content_async(request):
        pass


async def _strands_stream(model: Any) -> None:
    async for _ in model.stream([{"role": "user", "content": [{"text": "hi"}]}]):
        pass


async def _strands_agent(model: Any) -> None:
    from strands import Agent

    # The documented contract: the transport owns retries, so the agent's own
    # throttle retry (which takes every 429 for a throttle) is turned off.
    await Agent(model=model, retry_strategy=None, callback_handler=None).invoke_async("hi")


@dataclass(frozen=True)
class _Case:
    adapter: str
    probe: str
    call: Callable[[Donkey], Awaitable[Any] | Any]
    #: True when the framework sends through the shared transport, which retries
    #: a 503 itself; False when it builds its own client (retries turned off).
    shared_transport: bool = True
    #: Sends for the budget 429. Anything but 1 needs an ``exemption``.
    budget_sends: int = 1
    exemption: str | None = None


_CREWAI_RATE_LIMIT_EXEMPTION = (
    "crewai>=1.15.23 wraps every BaseLLM.call/acall in its own rate-limit retry "
    "(crewai.llms.retry, 3 attempts, 1s/2s backoff) and takes any 429 for a "
    "throttle. It has no setting to turn it off, so a budget refusal is sent 3 "
    "times. The provider SDK's own retries are off (max_retries=0)."
)


_CASES: dict[str, _Case] = {
    "raw-async": _Case(
        "llm",
        "openai",
        lambda d: d.llm.client().chat.completions.create(model="m", messages=_MESSAGES),
    ),
    "raw-sync": _Case(
        "llm",
        "openai",
        lambda d: d.llm.client(sync=True).chat.completions.create(model="m", messages=_MESSAGES),
    ),
    "langgraph-async": _Case(
        "langgraph", "langchain_openai", lambda d: d.langgraph.chat_model("m").ainvoke("hi")
    ),
    "langgraph-sync": _Case(
        "langgraph", "langchain_openai", lambda d: d.langgraph.chat_model("m").invoke("hi")
    ),
    "adk-model": _Case(
        "adk", "google.adk.models.lite_llm", lambda d: _adk_generate(d.adk.model("m"))
    ),
    "adk-gemini": _Case("adk", "google.adk.models", lambda d: _adk_generate(d.adk.gemini("m"))),
    "strands-model": _Case(
        "strands", "strands.models.openai", lambda d: _strands_stream(d.strands.model("m"))
    ),
    "strands-agent": _Case(
        "strands", "strands.models.openai", lambda d: _strands_agent(d.strands.model("m"))
    ),
    "agent_framework": _Case(
        "agent_framework",
        "agent_framework.openai",
        lambda d: d.agent_framework.chat_client("m").get_response("hi"),
    ),
    "openai_agents": _Case(
        "openai_agents",
        "agents",
        lambda d: d.openai_agents.connection_kwargs()["openai_client"].chat.completions.create(
            model="m", messages=_MESSAGES
        ),
    ),
    "anthropic": _Case(
        "anthropic",
        "anthropic",
        lambda d: d.anthropic.client().messages.create(
            model="m", max_tokens=1, messages=[{"role": "user", "content": "hi"}]
        ),
    ),
    "crewai-sync": _Case(
        "crewai",
        "crewai",
        lambda d: d.crewai.llm("m").call("hi"),
        shared_transport=False,
        budget_sends=3,
        exemption=_CREWAI_RATE_LIMIT_EXEMPTION,
    ),
    "crewai-async": _Case(
        "crewai",
        "crewai",
        lambda d: d.crewai.llm("m").acall("hi"),
        shared_transport=False,
        budget_sends=3,
        exemption=_CREWAI_RATE_LIMIT_EXEMPTION,
    ),
    "llamaindex-sync": _Case(
        "llamaindex", "llama_index.llms.openai_like", lambda d: d.llamaindex.llm("m").complete("hi")
    ),
    "llamaindex-async": _Case(
        "llamaindex",
        "llama_index.llms.openai_like",
        lambda d: d.llamaindex.llm("m").acomplete("hi"),
    ),
}


@pytest.mark.parametrize("name", list(_CASES))
async def test_framework_sends_a_refusal_once(name: str, proxy: _Proxy) -> None:
    case = _CASES[name]
    pytest.importorskip(case.probe)
    donkey = Donkey(
        DonkeyConfig(
            llm_proxy_url=proxy.url,
            llm_proxy_client_id="proxy-client-id",
            llm_proxy_client_secret="proxy-client-secret",
            max_retries=_MAX_RETRIES,
        )
    )
    try:
        with contextlib.suppress(Exception):
            result = case.call(donkey)
            if hasattr(result, "__await__"):
                await result
    finally:
        await donkey.aclose()

    if proxy.status == 429:
        expected = case.budget_sends
    else:
        expected = _MAX_RETRIES + 1 if case.shared_transport else 1
    assert proxy.sends == expected, (
        f"{name}: {proxy.sends} sends for a persistent {proxy.status}, expected {expected}"
    )


def test_every_adapter_has_a_retry_case() -> None:
    """A new adapter cannot ship without proving it sends a refusal once."""
    covered = {case.adapter for case in _CASES.values()}
    assert set(ADAPTERS) <= covered


def test_every_extra_send_is_an_asserted_exemption() -> None:
    """A case may expect more than one send for a refusal only with a reason."""
    unexplained = [n for n, c in _CASES.items() if c.budget_sends != 1 and not c.exemption]
    assert unexplained == []

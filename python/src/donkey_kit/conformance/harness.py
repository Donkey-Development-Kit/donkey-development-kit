"""The observable ``Donkey`` the conformance suite runs against (#191, `BG §1.5`).

The suite (:mod:`donkey_kit.conformance.suite`) says *what* to check; this
module owns *how* it is observed. For each scenario it builds a fresh ``Donkey``,
lets the customer's factory build an agent against it, arms the gateway to serve
one captured fixture for every send, runs ``agent.run(...)`` while capturing what
it raised and logged, and hands the resulting :class:`~.suite.Observation` to
the scenario's check.

It observes in one of two modes (#322, :data:`~.suite.MODES`):

- ``in-process`` (the default, #191): the gateway is a counting transport
  swapped onto the ``Donkey``'s own clients. Hermetic and fast; nothing leaves
  the process.
- ``server``: the gateway is the local gateway simulator (BG §1.4, the engine
  behind ``donkey mock``) on a real ephemeral port, booted once per run by
  :func:`~.gateway.start_gateway`. The ``Donkey`` points at it and sends over a
  real network hop with its own transport untouched; each scenario reconfigures
  the running simulator, and sends are counted from its request log.

The in-process mode is the in-process sibling of the local gateway simulator, and it reuses the
simulator's machinery so the injected refusal is the *same captured bytes*
``classify()`` is tested against (:func:`donkey_kit.simulator.inject.resolve_fixture`
gives us that fixture and asserts the classify() round-trip). The one deliberate
difference from ``simulate()``'s transport: this one counts *every* wire send
rather than deduping retries, because counting re-sends is exactly how the retry
scenario is observed.

Framework isolation (the layered architecture): imports only ``httpx``, the framework-free
``core``/``donkey`` surface, and the sibling simulator loader — never a web
framework and never ``openai`` at module top. It is a dev-only simulator-style
sibling, kept out of the five production layers by an import-linter contract.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import httpx

from .._testing import http_clients, resolved_adapters, swap_transport
from ..core import _wire
from ..core.config import DonkeyConfig
from ..core.errors import DonkeyError
from ..donkey import Donkey
from ..simulator.app import SIMULATOR_HEADER, SimulatorConfig
from ..simulator.fixtures import SHAPES, Fixture, load, replay_headers

# Reuse the simulator's single exception->fixture mapping (and its classify()
# round-trip assertion) rather than duplicating the table: if the mapping ever
# drifts, simulate() and this harness fail together — the "same files" rule.
from ..simulator.inject import resolve_fixture as _refusal_fixture
from ..simulator.scenarios import ScenarioHit
from .suite import (
    MODES,
    SCENARIOS,
    Mode,
    Observation,
    Outcome,
    Result,
    Scenario,
    validate_known_limitations,
)

if TYPE_CHECKING:  # the gateway module is loaded lazily, in server mode only
    from .gateway import Gateway

__all__ = [
    "DEFAULT_RUN_INPUT",
    "ConformanceHarness",
    "ConformanceUsageError",
    "offline_config",
    "run_conformance",
]

# A neutral prompt handed to every agent under test. It never reaches a real
# model — the gateway is the swapped transport or the local simulator — but
# agents that inspect their input want a real string.
DEFAULT_RUN_INPUT = "This is a Donkey conformance probe. Reply briefly."

# Always used in place of the customer's LLM proxy config (#737). The harness
# never connects to a real gateway (the transport is swapped, or in server mode
# the URL is the local simulator's), so these just need to be non-empty to
# satisfy validated(need="llm"). The ``.invalid`` TLD (RFC 2606) never resolves,
# so even a client the agent builds itself cannot reach a real host.
_PLACEHOLDER = "donkey-conformance"
_PLACEHOLDER_URL = "https://donkey-conformance.invalid"

AgentFactory = Callable[..., Any]


class ConformanceUsageError(Exception):
    """The agent under test does not meet the harness contract (e.g. no callable
    ``run()``). A setup problem to fix, not a scenario finding — it is surfaced
    as a hard error, never a fail verdict."""


def offline_config(*, proxy_url: str = _PLACEHOLDER_URL) -> DonkeyConfig:
    """The customer's own config with every endpoint credential replaced (#737).

    Non-secret settings (model catalog, attribution, header names) are kept so
    the agent is built as it would be in production. The proxy URL and its
    credentials are *always* the placeholders, whatever the environment says: an
    agent that builds its own client from the config it was handed then points at
    a host that cannot resolve, not at the customer's live gateway. The
    control-plane pair is cleared too, so ``Donkey`` builds no Anypoint token
    fetcher, and the auth mode is pinned to client-id so a model-wallet JWT is
    never needed. Server mode passes the local simulator's URL as ``proxy_url``
    (#322); the credentials stay placeholders."""
    cfg = DonkeyConfig.from_env()
    # Replace with named fields (not **dict) so the types stay checkable.
    return replace(
        cfg,
        llm_proxy_url=proxy_url,
        llm_proxy_client_id=_PLACEHOLDER,
        llm_proxy_client_secret=_PLACEHOLDER,
        llm_proxy_key=None,
        llm_proxy_auth="client-id",
        llm_proxy_wallet_client_id=None,
        client_id=None,
        client_secret=None,
    )


_CHAT_COMPLETIONS_ROUTE = "/chat/completions"


def _as_chat_completion(body: bytes) -> bytes:
    """Re-shape the captured ``/responses`` success body as a Chat Completions one.

    The captured success is a Responses-API body. A framework that calls
    ``/chat/completions`` (LiteLLM, Strands' ``OpenAIModel``, the Agents SDK's chat
    model, LlamaIndex's ``OpenAILike``) cannot parse that shape, so the same
    captured model, text and token counts are served in the shape that route
    returns. Anything that is not the expected shape is served as captured."""
    try:
        captured = json.loads(body)
        text = "".join(
            part["text"]
            for item in captured["output"]
            if item.get("type") == "message"
            for part in item["content"]
            if part.get("type") == "output_text"
        )
        usage = captured["usage"]
        return json.dumps(
            {
                "id": captured["id"],
                "object": "chat.completion",
                "created": captured["created_at"],
                "model": captured["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": usage["input_tokens"],
                    "completion_tokens": usage["output_tokens"],
                    "total_tokens": usage["total_tokens"],
                },
            }
        ).encode()
    except (KeyError, TypeError, ValueError):
        return body


def _fixture_responder(
    fixture: Fixture, *, strip_budget: bool = False
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a responder that replays one captured fixture, honesty-stamped.

    Uses the same header allow-list (:func:`replay_headers`) and honesty stamp
    (``x-donkey-simulator: true``) as ``simulate()`` and the mock server, so a
    conformance response is never mistaken for a live gateway response. With
    ``strip_budget`` the ``x-token-*`` headers and ``x-llm-proxy-ratelimit`` are
    dropped, the two the token window is read from, so the run observes a
    genuinely unobserved budget even if a future capture grows them."""

    def respond(request: httpx.Request) -> httpx.Response:
        headers = replay_headers(fixture)
        if strip_budget:
            headers = {
                k: v
                for k, v in headers.items()
                if not k.startswith(_wire.TOKEN_HEADER_PREFIX) and k != _wire.RATELIMIT_HEADER
            }
        if fixture.content_type is not None:
            headers["content-type"] = fixture.content_type
        headers[SIMULATOR_HEADER] = "true"
        content = fixture.body
        if fixture.status == 200 and request.url.path.endswith(_CHAT_COMPLETIONS_ROUTE):
            content = _as_chat_completion(content)
        return httpx.Response(
            status_code=fixture.status,
            headers=headers,
            content=content,
            request=request,
        )

    return respond


class _ProbeTransport(httpx.AsyncBaseTransport, httpx.BaseTransport):
    """Serves a fixed captured response for every send, counting wire sends.

    Deliberately does NOT dedupe by request identity the way ``simulate()``'s
    transport does: the retry scenario is observed by counting re-sends, so every
    ``handle_request`` increments :attr:`wire_sends`. Implements both transport
    protocols so one instance fronts a ``DonkeyClient`` and a
    ``DonkeyAsyncClient`` at once (and its count spans both)."""

    def __init__(self, responder: Callable[[httpx.Request], httpx.Response]) -> None:
        self._responder = responder
        self.wire_sends = 0

    def _serve(self, request: httpx.Request) -> httpx.Response:
        self.wire_sends += 1
        return self._responder(request)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._serve(request)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._serve(request)


class _EveryCall:
    """A simulator fault scenario that serves one captured shape on every model
    call (server mode, #322) — the simulator-side twin of :class:`_ProbeTransport`
    serving a fixed fixture. Meets :class:`~donkey_kit.simulator.scenarios.FaultScenario`."""

    def __init__(self, shape: str) -> None:
        self.name = f"conformance:{shape}"
        self._shape = shape

    def on_call(self, text: str) -> ScenarioHit:  # noqa: ARG002 - FaultScenario protocol
        """Serve the armed shape, whatever the request says."""
        return ScenarioHit(self._shape)


# The httpx-family stacks whose real network transports the harness blocks.
# ``httpx2`` is Pydantic's continuation of httpx with its own classes; openai 3.x
# and anthropic 1.x build their clients on it, so blocking httpx alone would let
# a stock OpenAI client straight through (#737).
_HTTP_STACKS = ("httpx", "httpx2")


def _installed_http_stacks() -> list[Any]:
    """The httpx-family modules that are installed, imported now so a client the
    agent builds later in the scenario gets the patched classes."""
    stacks: list[Any] = []
    for name in _HTTP_STACKS:
        if importlib.util.find_spec(name) is not None:
            stacks.append(importlib.import_module(name))
    return stacks


def _origin(url: Any) -> tuple[str, str, int | None]:
    """The scheme, host and port of an httpx-family URL (``httpx.URL`` or httpx2's)."""
    return (str(url.scheme), str(url.host), url.port)


@contextmanager
def _block_real_transports(attempts: list[str], *, allow: str | None = None) -> Iterator[None]:
    """Refuse every send that reaches a real network transport (#737).

    The Donkey's own clients are swapped onto the counting probe, so anything
    that arrives at ``HTTPTransport`` / ``AsyncHTTPTransport`` (of httpx, or of
    httpx2 when installed) came from a client the agent built itself: a
    framework that owns its transport, or a stock ``httpx``/OpenAI client. Each
    one is recorded in ``attempts`` and failed with that stack's own
    ``ConnectError``, the error the agent would see if the host were down, so
    nothing leaves the process. The patch is on the classes, so it also covers
    clients built in other threads, and it is removed on exit.

    ``allow`` is server mode's one exception (#322): a send to that origin — the
    local simulator — goes to the real transport, so the ``Donkey``'s client
    reaches the simulator over a real network hop. Every other host is still
    refused."""
    restore: list[tuple[type, str, Any]] = []
    allowed = _origin(httpx.URL(allow)) if allow is not None else None

    def block(stack: Any) -> None:
        sync_send = stack.HTTPTransport.__dict__["handle_request"]
        async_send = stack.AsyncHTTPTransport.__dict__["handle_async_request"]

        def refuse(request: Any) -> Exception:
            attempts.append(f"{request.method} {request.url}")
            error: Exception = stack.ConnectError(
                "blocked by the Donkey conformance harness: this request did not "
                "go through the Donkey client",
                request=request,
            )
            return error

        def blocked_sync(self: Any, request: Any) -> Any:
            if allowed is not None and _origin(request.url) == allowed:
                return sync_send(self, request)
            raise refuse(request)

        async def blocked_async(self: Any, request: Any) -> Any:
            if allowed is not None and _origin(request.url) == allowed:
                return await async_send(self, request)
            raise refuse(request)

        for cls, method, original, blocked in (
            (stack.HTTPTransport, "handle_request", sync_send, blocked_sync),
            (stack.AsyncHTTPTransport, "handle_async_request", async_send, blocked_async),
        ):
            restore.append((cls, method, original))
            setattr(cls, method, blocked)

    try:
        for stack in _installed_http_stacks():
            block(stack)
        yield
    finally:
        for cls, method, original in reversed(restore):
            setattr(cls, method, original)


# Standard LogRecord attribute names, so :func:`_render_records` can tell an
# ``extra={...}`` field apart from the built-in ones and include it in the scan.
_STD_LOGRECORD_KEYS = frozenset(vars(logging.makeLogRecord({})).keys())


class _CaptureHandler(logging.Handler):
    """Collects every record emitted anywhere during a run, for the correlation
    scenario to grep."""

    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.DEBUG)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(record)


def _render_records(records: list[logging.LogRecord]) -> str:
    """Flatten captured records into one searchable string: the formatted
    message plus the stringified value of every ``extra=`` field, so an id logged
    either in the message or via ``extra={"correlation_id": ...}`` is found."""
    parts: list[str] = []
    for record in records:
        try:
            parts.append(record.getMessage())
        except Exception:  # noqa: BLE001 — a bad format string must not abort capture
            parts.append(str(record.msg))
        parts.extend(
            str(value) for key, value in record.__dict__.items() if key not in _STD_LOGRECORD_KEYS
        )
    return "\n".join(parts)


def _build_agent(factory: AgentFactory, donkey: Donkey) -> Any:
    """Call the customer's factory, passing the ``Donkey`` iff it declares a slot
    for it — the app-factory / dependency-injection pattern (``gunicorn
    app:create_app``). ``build(donkey)`` and ``build(*, donkey=...)`` receive it;
    a zero-argument ``build()`` does not."""
    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        # Un-introspectable (some builtins/C callables): assume it wants donkey.
        return factory(donkey)
    if "donkey" in params and params["donkey"].kind is inspect.Parameter.KEYWORD_ONLY:
        return factory(donkey=donkey)
    accepts_positional = any(
        p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for p in params.values()
    ) or any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params.values())
    return factory(donkey) if accepts_positional else factory()


class ConformanceHarness:
    """Runs one scenario against a freshly built agent. Implements
    :class:`~.suite.ScenarioContext`: the scenario check calls
    :meth:`serve_refusal`/:meth:`serve_success` to arm the gateway and
    :meth:`run` to drive the agent.

    With a ``gateway`` (server mode, #322) the gateway is that running local
    simulator; without one it is a counting transport swapped onto the
    ``Donkey``'s clients (in-process mode)."""

    def __init__(
        self,
        agent_factory: AgentFactory,
        *,
        run_input: str = DEFAULT_RUN_INPUT,
        gateway: Gateway | None = None,
    ) -> None:
        self._factory = agent_factory
        self._run_input = run_input
        self._gateway = gateway
        self._donkey: Donkey | None = None
        self._agent: Any = None
        self._probe: _ProbeTransport | None = None
        self._bypass_attempts: list[str] = []

    async def run_scenario(self, scenario: Scenario) -> Outcome:
        """Build a fresh ``Donkey`` + agent, run the scenario's check against
        this harness, and tear the ``Donkey`` down — so no transport swap, budget
        state, or agent state leaks between scenarios. Real network transports
        are blocked from before the agent is built until it is torn down; in
        server mode the local simulator's origin is the one exception."""
        gateway = self._gateway
        if gateway is None:
            self._donkey = Donkey(offline_config())
        else:
            self._donkey = Donkey(offline_config(proxy_url=gateway.url))
            gateway.configure()  # a fresh, unarmed simulator per scenario
            gateway.reset()
        self._probe = None
        self._bypass_attempts = []
        allow = gateway.url if gateway is not None else None
        try:
            with _block_real_transports(self._bypass_attempts, allow=allow):
                try:
                    self._agent = _build_agent(self._factory, self._donkey)
                    return await scenario.check(self)
                finally:
                    await self._donkey.aclose()
        finally:
            self._donkey = None
            self._agent = None
            self._probe = None

    # --- ScenarioContext surface -------------------------------------------
    @property
    def agent(self) -> Any:
        """The agent the factory built for the current scenario."""
        return self._agent

    def serve_refusal(self, error: type[DonkeyError]) -> None:
        """Arm the gateway to answer the next model call with the refusal for ``error``:
        its captured fixture, or a ``classify.register()`` entry's example (#210).

        Raises:
            ConformanceUsageError: In server mode, ``error`` has no captured
                shape the simulator can serve (a ``classify.register()`` type).
        """
        fixture = _refusal_fixture(error)
        if self._gateway is None:
            self._arm(_fixture_responder(fixture))
            return
        if fixture.shape not in SHAPES:
            raise ConformanceUsageError(
                f"server mode cannot serve {error.__name__}: the local simulator "
                f"replays captured shapes only, and a classify.register() type has "
                f"none. Run this scenario with --donkey-mode in-process."
            )
        self._gateway.configure(SimulatorConfig(scenarios=(_EveryCall(fixture.shape),)))
        self._gateway.reset()

    def serve_success(self, *, budget_headers: bool = True) -> None:
        """Arm the gateway to answer with the captured success response.

        ``budget_headers=False`` serves it with no budget headers: in-process the
        ``x-token-*`` headers are stripped; in server mode the simulator's
        synthesised ``x-llm-proxy-ratelimit`` window is turned off.
        """
        if self._gateway is None:
            self._arm(_fixture_responder(load("success"), strip_budget=not budget_headers))
            return
        self._gateway.configure(SimulatorConfig(budget_window=budget_headers))
        self._gateway.reset()

    async def run(self, *, correlation_id: str | None = None) -> Observation:
        """Drive ``agent.run(<input>)`` once and return what the harness observed.

        Raises:
            ConformanceUsageError: The agent has no callable ``run`` method.
        """
        agent = self._agent
        run = getattr(agent, "run", None)
        if not callable(run):
            raise ConformanceUsageError(
                f"the agent your factory returned has no callable run(...) method "
                f"(got {type(agent).__name__}); the conformance suite drives "
                f"agent.run(<input>). Adapt your agent or expose a run() shim."
            )
        records: list[logging.LogRecord] = []
        handler = _CaptureHandler(records)
        root = logging.getLogger()
        previous_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)  # let INFO/DEBUG records reach the handler
        raised: BaseException | None = None
        returned: Any = None
        bind = (
            self._donkey.run(correlation_id)
            if correlation_id is not None and self._donkey is not None
            else nullcontext()
        )
        try:
            with bind:
                result = run(self._run_input)
                returned = await result if inspect.isawaitable(result) else result
        except ConformanceUsageError:
            raise
        except Exception as exc:  # noqa: BLE001 — we observe whatever the agent lets escape
            raised = exc
        finally:
            root.removeHandler(handler)
            root.setLevel(previous_level)
        return Observation(
            returned=returned,
            raised=raised,
            wire_sends=self._wire_sends(),
            log_text=_render_records(records),
            bypass_attempts=len(self._bypass_attempts),
            unobservable_adapters=self._unobservable_adapters(),
        )

    def _wire_sends(self) -> int:
        """Sends since the gateway was armed: counted by the probe transport
        in-process, and from the simulator's request log in server mode (#322),
        where there is no in-process transport to count."""
        if self._gateway is not None:
            return self._gateway.requests_received
        return self._probe.wire_sends if self._probe is not None else 0

    def _unobservable_adapters(self) -> tuple[str, ...]:
        """The adapters the agent resolved on this ``Donkey`` whose model calls
        do not pass through its transport (``observes_last_call`` is False), so
        the probe can never see them."""
        donkey = self._donkey
        if donkey is None:
            return ()
        return tuple(
            sorted(
                name
                for name, adapter in resolved_adapters(donkey).items()
                if not adapter.observes_last_call
            )
        )

    def _arm(self, responder: Callable[[httpx.Request], httpx.Response]) -> None:
        """Swap a counting probe onto both transports. The sync client is built
        eagerly (it opens no connection until used) so a blocking agent's client
        is armed too, and both share one probe so the wire-send count spans
        async and sync."""
        donkey = self._donkey
        assert donkey is not None  # set by run_scenario before any check runs
        self._probe = _ProbeTransport(responder)
        for client in http_clients(donkey):
            swap_transport(client, self._probe)


@contextmanager
def _gateway_for(mode: Mode) -> Iterator[Gateway | None]:
    """The running local simulator for a ``server`` run (stopped on exit), or
    ``None`` in-process. The gateway module — and ``uvicorn`` behind it — loads
    only here, so an in-process run never imports either."""
    if mode == "in-process":
        yield None
        return
    from .gateway import start_gateway

    gateway = start_gateway()
    try:
        yield gateway
    finally:
        gateway.close()


async def run_conformance(
    agent_factory: AgentFactory,
    *,
    known_limitations: object = None,
    run_input: str = DEFAULT_RUN_INPUT,
    mode: Mode = "in-process",
) -> list[Result]:
    """Run every scenario against ``agent_factory`` and return the table rows.

    A scenario listed in ``known_limitations`` is reported ``exempt`` with its
    asserted reason and is *not* run (the exemption replaces the check); every
    other scenario runs and is reported ``pass``/``fail``. ``known_limitations``
    is validated up front, so a bad key or empty reason raises before any agent
    is built.

    ``mode="server"`` (#322) runs every scenario against one local gateway
    simulator on an ephemeral port instead of an in-process transport; it needs
    the ``[local]`` extra and raises the :class:`ImportError` naming it otherwise.

    Raises:
        ValueError: ``mode`` is not one of :data:`~.suite.MODES`.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {list(MODES)}, got {mode!r}")
    known = validate_known_limitations(known_limitations)
    results: list[Result] = []
    with _gateway_for(mode) as gateway:
        for scenario in SCENARIOS:
            if scenario.name in known:
                results.append(
                    Result(scenario.name, scenario.title, "exempt", known[scenario.name])
                )
                continue
            harness = ConformanceHarness(agent_factory, run_input=run_input, gateway=gateway)
            outcome = await harness.run_scenario(scenario)
            results.append(
                Result(
                    scenario.name,
                    scenario.title,
                    "pass" if outcome.passed else "fail",
                    outcome.detail,
                )
            )
    return results

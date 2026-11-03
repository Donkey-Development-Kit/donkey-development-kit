"""``build_app`` — the local gateway simulator's ASGI application (BG §1.4).

A pure-Python HTTP stand-in for the governed LLM proxy. It **replays the same
captured fixtures** ``core.errors.classify()`` is tested against (via
:mod:`donkey_kit.simulator.fixtures`) so a stock ``openai``/``httpx`` client —
or ``donkey.llm.client()`` — sees byte-identical rejection bodies and the exact
discriminator headers, and the SDK's typed refusals light up locally without a
real Anypoint sandbox.

It serves the two OpenAI-format model-call routes: ``POST …/responses`` and
``POST …/chat/completions`` (#895), each non-streaming and streaming, and the
two native ingresses (#1562): Anthropic's ``POST …/v1/messages`` and Gemini's
``POST …/models/{model}:generateContent`` / ``:streamGenerateContent``. Any
other route gets the captured ``/models`` 404.

What it is NOT: it enforces no policy, evaluates nothing, and forwards no live
traffic. It is a separate process serving static fixtures. Every response
carries ``x-donkey-simulator: true`` so it can never be mistaken for a real
gateway (BG §1.4, non-negotiable).

Framework isolation (the layered architecture): ``starlette`` is imported **lazily inside**
:func:`build_app`, never at module top, so ``import donkey_kit.simulator``
stays green under the base-only CI job (the ``dev`` group only, no ``[local]`` extra).
The public return type is a framework-free ASGI ``Protocol`` so no ``starlette``
type leaks across the boundary under ``mypy --strict``.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from .fixtures import (
    RATELIMIT_HEADER,
    Fixture,
    load,
    render_ratelimit_prose,
    replay_headers,
)
from .scenarios import BudgetScenario, FaultScenario, RequestLimitScenario, request_text

__all__ = [
    "RATELIMIT_HEADER",
    "SIMULATOR_HEADER",
    "SIM_MODEL_PREFIX",
    "ASGIApp",
    "SimulatorConfig",
    "build_app",
]

# BG §1.4 honesty rule: stamped on EVERY response so nothing the simulator emits
# can be mistaken for a real gateway. The bytes form is used by the ASGI wrapper
# (:class:`_HonestyStamp`) that also stamps framework-generated 405/500 responses.
SIMULATOR_HEADER = "x-donkey-simulator"
_SIMULATOR_HEADER_BYTES = SIMULATOR_HEADER.encode("latin-1")

# The ONE fixed, documented, simulator-owned rejection-selection trigger (the
# configurable --scenario engine is a linked follow-up, not #187). A request
# whose model id is "donkey-sim/<shape>" is served that rejection shape. This is
# a simulator control surface only — NEVER a real Omni Gateway behaviour (verification discipline).
SIM_MODEL_PREFIX = "donkey-sim/"

# The budget window the live proxy emits on a happy-path `200` (with the
# `llm-token-rate-limit` policy applied) is a single prose header,
# `x-llm-proxy-ratelimit`, NOT the numeric `x-token-*` trio (which the live gateway
# emits only on the `429`). The name is defined once in `core.budget` (which parses
# it, #352) and imported here so the simulator renders what the client parses. The
# format string is fixed by the live/fixture capture (#352/#353):
#   "Token rate limit: {remaining} tokens remaining of {limit} limit. Reset in {ms}ms."

# Shapes selectable via the model-id sentinel: the documented rejections
# plus the consumer-auth 401 and the gateway's bare-model-name 400 (#891).
_REJECTION_SHAPES = frozenset(
    {
        "token-rate-limit",
        "request-rate-limit",
        "pii-detected",
        "injection-protection",
        "regex-prompt-guard",
        "content-safety",
        "content-moderation",
        "agent-killed",
        "ciba-pending",
        "ciba-denied",
        "model-not-found",
        "model-not-routable",
        "upstream-5xx",
        "client-id-missing",
    }
)

# Happy-path (2xx) shapes ALSO selectable via the model-id sentinel: the
# SEMANTIC-routing 200, so a developer can drive the ``matched_topic`` /
# ``routing_score`` branch of ``donkey.last_call`` offline (#601). Kept apart from
# the rejections because these are 200s; both are served as-is from the captured
# fixture (no synthesised budget window), so the replay stays byte-faithful.
# The two qualified-vendor 200s (#1412) drive a ``groupId:vendor`` provider
# (``mulesoft:openai``) on a matched route and on the policy fallback.
_HAPPY_SHAPES = frozenset(
    {"success-semantic", "success-qualified-vendor", "fallback-qualified-vendor"}
)

# Every shape the model-id sentinel can force to a specific captured fixture.
_SENTINEL_SHAPES = _REJECTION_SHAPES | _HAPPY_SHAPES

# The rejections a native ingress can be forced to serve (#1562): the gateway's
# own policy answers. The OpenAI upstream's model-not-found 400 and the
# bare-model-name 400 are left out, because a native ingress serves an
# unresolvable model on its own vendor instead (docs/verified-apis.md §2).
_NATIVE_REJECTION_SHAPES = _REJECTION_SHAPES - {"model-not-found", "model-not-routable"}


@dataclass(frozen=True)
class _Route:
    """The fixture shapes one model-call route serves: its happy-path 200 and
    stream, the shapes its model-id sentinel may force, and, on a native
    ingress, the captured 400 for a body that is not JSON."""

    success: str
    stream: str
    sentinels: frozenset[str] = _SENTINEL_SHAPES
    invalid_json: str | None = None


_RESPONSES_ROUTE = _Route(success="success", stream="stream")
# Chat Completions (#895): the non-streaming 200 is the live capture behind the
# docs/verified-apis.md §2 Chat Completions row; the stream is OpenAI's public
# shape until #894 captures one.
_CHAT_COMPLETIONS_ROUTE = _Route(success="chat-success", stream="chat-stream")
# The native ingresses on model-based-routing 1.0.5 (#1562), from the two-route
# live captures. Anthropic adds the policy-fallback 200 as a sentinel; on the
# Gemini ingress every path model was served by gemini, so it has none.
_ANTHROPIC_ROUTE = _Route(
    success="anthropic-success",
    stream="anthropic-stream",
    sentinels=_NATIVE_REJECTION_SHAPES | {"anthropic-fallback"},
    invalid_json="anthropic-invalid-json",
)
_GEMINI_ROUTE = _Route(
    success="gemini-success",
    stream="gemini-stream",
    sentinels=_NATIVE_REJECTION_SHAPES,
    invalid_json="gemini-invalid-json",
)
# Gemini names the model in the path and streams by method, not by a body flag.
_GEMINI_PATH = re.compile(r"/models/(.+):(generateContent|streamGenerateContent)$")


class ASGIApp(Protocol):
    """The framework-free ASGI callable :func:`build_app` returns — so callers
    (``httpx.ASGITransport``, ``uvicorn.run``) and ``mypy`` never see a
    ``starlette`` type."""

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        """Handle one ASGI connection."""
        ...


@dataclass(frozen=True)
class SimulatorConfig:
    """Tunables for the happy-path budget window (BG §1.4 / #185/#186/#353).

    The live ``200`` (with the ``llm-token-rate-limit`` policy applied) carries the
    budget window as a single prose ``x-llm-proxy-ratelimit`` header, not the numeric
    ``x-token-*`` trio (#352). The simulator renders that same sentence from a
    plausible, monotonically decreasing counter so a developer can exercise
    :class:`~donkey_kit.Budget` and its pacing locally against the *observed* live
    contract — the fields below drive ``{remaining}``/``{limit}``/``{ms}`` in the
    rendered sentence.
    """

    token_limit: int = 100_000
    token_step: int = 500
    token_reset_ms: int = 60_000
    # Whether the happy path carries the synthesised budget window above. False
    # serves the success exactly as captured, with no ``x-llm-proxy-ratelimit``:
    # a proxy without the ``llm-token-rate-limit`` policy (the conformance kit's
    # "works without budget headers" scenario, #322). A ``budget`` scenario still
    # owns its own header either way.
    budget_window: bool = True
    # Scripted fault-injection rules applied per model call (#188), on both
    # POST /responses and POST /chat/completions (#895). Parsed
    # from the CLI's repeatable --scenario flag. Stateful and single-use: one set
    # drives one simulator instance (see donkey_kit.simulator.scenarios).
    scenarios: tuple[FaultScenario, ...] = ()


class _Simulator:
    """Holds the mutable budget counter (guarded by an ``asyncio.Lock`` so
    concurrent requests can't race it) and builds every response."""

    def __init__(self, config: SimulatorConfig) -> None:
        self._config = config
        self._remaining = config.token_limit
        self._lock = asyncio.Lock()
        # Split scenarios: rejection rules run first (in injection-before-pii
        # precedence, independent of CLI order), then the single request-limit
        # scenario (#974), then the single budget scenario (#188). Each window
        # scenario, when it passes, adds its own headers to the happy path; a
        # budget scenario's prose header replaces the default synthesised counter.
        self._request_limit: RequestLimitScenario | None = next(
            (s for s in config.scenarios if isinstance(s, RequestLimitScenario)), None
        )
        self._budget: BudgetScenario | None = next(
            (s for s in config.scenarios if isinstance(s, BudgetScenario)), None
        )
        _order = {"injection": 0, "pii_block": 1}
        self._reject_scenarios: list[FaultScenario] = sorted(
            (
                s
                for s in config.scenarios
                if not isinstance(s, (BudgetScenario, RequestLimitScenario))
            ),
            key=lambda s: _order.get(s.name, 99),
        )

    def _response(self, fixture: Fixture, extra: dict[str, str] | None = None) -> Any:
        """Build a starlette Response for a resolved fixture, always honesty-stamped."""
        from starlette.responses import Response

        headers = replay_headers(fixture)
        if extra:
            headers.update(extra)
        headers[SIMULATOR_HEADER] = "true"
        return Response(
            content=fixture.body,
            status_code=fixture.status,
            headers=headers,
            media_type=fixture.content_type,
        )

    async def _synth_ratelimit_header(self) -> dict[str, str]:
        """Next budget window as the live ``x-llm-proxy-ratelimit`` prose header,
        decrementing the counter under the lock (#353)."""
        async with self._lock:
            self._remaining = max(0, self._remaining - self._config.token_step)
            remaining = self._remaining
        sentence = render_ratelimit_prose(
            remaining, self._config.token_limit, self._config.token_reset_ms
        )
        return {RATELIMIT_HEADER: sentence}

    async def dispatch(self, request: Any) -> Any:
        path = request.url.path
        if request.method == "POST" and path.endswith("/responses"):
            return await self._model_call(request, _RESPONSES_ROUTE)
        if request.method == "POST" and path.endswith("/chat/completions"):
            return await self._model_call(request, _CHAT_COMPLETIONS_ROUTE)
        if request.method == "POST" and path.endswith("/v1/messages"):
            return await self._model_call(request, _ANTHROPIC_ROUTE)
        gemini = _GEMINI_PATH.search(path) if request.method == "POST" else None
        if gemini is not None:
            return await self._model_call(
                request,
                _GEMINI_ROUTE,
                path_model=gemini.group(1),
                stream=gemini.group(2) == "streamGenerateContent",
            )
        if request.method == "GET" and path.endswith("/models"):
            # No catalog endpoint is verified; mirror the captured 404 rather
            # than fabricate a model list (verification discipline).
            return self._response(load("models-notfound"))
        # Unknown route: an honest, honesty-stamped 404.
        return self._response(load("models-notfound"))

    async def _model_call(
        self,
        request: Any,
        route: _Route,
        *,
        path_model: str | None = None,
        stream: bool | None = None,
    ) -> Any:
        """Serve one model call on any route (#895, #1562). The sentinel, the
        scenario rules and the budget window are the same on all of them: a
        rejection is the gateway's policy answer, and the gateway applies its
        policies before it picks an upstream API. Only the happy-path bodies
        differ. ``path_model`` and ``stream`` are Gemini's, which names the
        model in the path and streams by method."""
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 — a malformed/empty body is just "happy path"
            if route.invalid_json is not None:
                # A native ingress answers its captured flat-string 400 (#1562).
                return self._response(load(route.invalid_json))
            payload = {}
        model = path_model
        if model is None and isinstance(payload, dict):
            model = payload.get("model")
        if isinstance(model, str) and model.startswith(SIM_MODEL_PREFIX):
            shape = model[len(SIM_MODEL_PREFIX) :]
            if shape in route.sentinels:
                # The model-id sentinel is an explicit "force this exact shape"
                # override and wins over ambient scenario fault-injection. It
                # selects a rejection shape or a happy-path variant (the
                # Semantic-routing 200, #601); both are served as-is from the
                # captured fixture.
                return self._response(load(shape))
            # Unknown sentinel suffix falls through to the scenario/happy path.

        # Scenario fault-injection (#188): rejection rules first (injection, then
        # pii_block), then the request-limit scenario (#974), then the budget
        # scenario; the first hit short-circuits.
        window_headers: dict[str, str] = {}
        text = request_text(payload)
        for scenario in self._reject_scenarios:
            hit = scenario.on_call(text)
            if hit is not None:
                return self._response(load(hit.shape), extra=hit.extra_headers)
        if self._request_limit is not None:
            hit = self._request_limit.on_call(text)
            if hit is not None:
                return self._response(load(hit.shape), extra=hit.extra_headers)
            window_headers.update(self._request_limit.happy_path_headers())
        if self._budget is not None:
            hit = self._budget.on_call(text)
            if hit is not None:
                return self._response(load(hit.shape), extra=hit.extra_headers)
            # Budget passed: it owns the happy-path prose ratelimit header.
            window_headers.update(self._budget.happy_path_headers())
        elif self._config.budget_window:
            window_headers.update(await self._synth_ratelimit_header())
        if stream is None:
            stream = isinstance(payload, dict) and payload.get("stream") is True
        return self._happy(route, window_headers, stream=stream)

    def _happy(self, route: _Route, ratelimit_header: dict[str, str], *, stream: bool) -> Any:
        """Serve the route's happy-path 200 (or its stream), carrying the given
        window headers (the token prose header, and the request trio when a
        request-limit scenario is on)."""
        if stream:
            # /responses: the captured stream sample is a single, truncated
            # `response.created` event — a real capture, NOT a complete SSE
            # stream ending in `data: [DONE]`. It is replayed verbatim rather than
            # fabricating the remaining events (verification discipline: never
            # invent gateway output); a complete SSE capture is a follow-up.
            # /chat/completions: OpenAI's public chunk shape, not a capture (see
            # the "chat-stream" row in fixtures.SHAPES). The native streams are
            # complete live captures (#1562). Each carries the budget window
            # prose header, like any happy path.
            return self._response(load(route.stream), extra=ratelimit_header)
        return self._response(load(route.success), extra=ratelimit_header)


class _HonestyStamp:
    """ASGI wrapper enforcing BG §1.4's *non-negotiable* honesty rule on **every**
    response — including the ``405`` (unsupported method) and ``500`` that
    starlette generates itself, which never pass through
    :meth:`_Simulator._response`. It adds ``x-donkey-simulator: true`` to any
    ``http.response.start`` that does not already carry it (so it never
    duplicates the header ``_response`` already sets)."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":  # lifespan/websocket: pass through untouched
            await self._app(scope, receive, send)
            return

        async def _stamped(message: MutableMapping[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = message.setdefault("headers", [])
                if not any(key == _SIMULATOR_HEADER_BYTES for key, _ in headers):
                    headers.append((_SIMULATOR_HEADER_BYTES, b"true"))
            await send(message)

        await self._app(scope, receive, _stamped)


def build_app(config: SimulatorConfig | None = None) -> ASGIApp:
    """Build the simulator's ASGI app. ``starlette`` is imported here, lazily, so
    the module stays importable without the ``[local]`` extra.

    Drive it in-process with ``httpx.ASGITransport(app=build_app())`` for
    zero-network tests, or hand it to ``uvicorn`` via
    :func:`donkey_kit.simulator.server.serve` for a real TCP port.

    The app is wrapped in :class:`_HonestyStamp` so the ``x-donkey-simulator``
    header lands on framework-generated ``405``/``500`` responses too.
    """
    from starlette.applications import Starlette
    from starlette.routing import Route

    sim = _Simulator(config or SimulatorConfig())
    routes = [Route("/{path:path}", sim.dispatch, methods=["GET", "POST"])]
    return _HonestyStamp(cast(ASGIApp, Starlette(routes=routes)))

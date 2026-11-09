"""An out-of-process local gateway simulator a test can assert against (#278, `BG §1.5`).

``simulate()`` (#190) and the in-process ``donkey`` fixture (#191) cover anything
that calls the SDK in-process. They cannot cover the cases the vision deck names
explicitly — a containerised agent, a Node service, an A2A client, a manual
``curl`` — none of which import ``donkey_kit``. Those need a **real listener on a
real port**, plus a way to ask the gateway what it *received* ("did my agent stop
after the refusal, or retry four more times?").

This module boots the same local gateway simulator
(:func:`donkey_kit.simulator.build_app`, BG §1.4) that ``simulate()`` and
``donkey mock`` replay — so a stock client sees byte-identical rejection shapes —
on an **ephemeral port** (bind ``0``) in a daemon thread, and wraps it in a
request-recording ASGI spy the test can assert on. The public entry point is the
``gateway`` pytest fixture in :mod:`~donkey_kit.conformance.plugin`; this module
is imported lazily from there so the auto-loaded plugin never drags ``uvicorn``
onto every pytest run.

Framework isolation (the layered architecture): ``uvicorn`` is imported lazily
inside :func:`start_gateway`, and ``starlette`` only inside the simulator's own
``build_app`` — so ``import donkey_kit.conformance.gateway`` stays green under the
base-only CI job (the ``dev`` group only, no ``[local]`` extra). Taking the fixture
without that extra raises an :class:`ImportError` naming the exact
``pip install`` — never a bare ``ModuleNotFoundError`` and never a silent skip.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any

from ..core import _wire
from ..core.config import DonkeyConfig
from ..simulator.app import ASGIApp, SimulatorConfig, build_app
from ..simulator.scenarios import FaultScenario, parse_scenario

__all__ = ["Gateway", "RecordedRequest", "start_gateway"]

# The one extra that makes the out-of-process path real: starlette (the ASGI app)
# + uvicorn (the server). Both live in `[local]`, so the hint names that extra —
# not `[test]`, which ships the fixture itself but not the server it drives.
_LOCAL_EXTRA_HINT = (
    "Server mode (the `gateway` fixture, `--donkey-mode server` or "
    '`run_conformance(mode="server")`) needs the local simulator server extra '
    "(starlette + uvicorn). Install it with:\n"
    '    pip install "donkey-kit[local]"'
)

# Only names the SDK forbids for custom key/user-token headers survive recording.
# Correlation, call-id and cost names can be repurposed after their configured
# names move, so their values must be redacted even without the caller's config.
_RECORDED_HEADERS = frozenset(
    name.lower()
    for name in (
        "content-type",
        _wire.LLM_PROXY_CLIENT_ID_HEADER,
        _wire.LLM_PROXY_WALLET_CLIENT_ID_HEADER,
        _wire.ATTRIBUTION_APP_HEADER_PLACEHOLDER,
        _wire.ATTRIBUTION_BUSINESS_GROUP_HEADER_PLACEHOLDER,
    )
)
_REDACTED = "***"

# How long to wait for uvicorn to report `started` before giving up.
_STARTUP_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class RecordedRequest:
    """One request the gateway received, as seen from the gateway's side.

    ``headers`` retains only client/wallet IDs, reserved application/business-
    group attribution and content-type values. Correlation, call-ID, cost-tag
    and all other values are redacted, even without ``recording_config``.
    Those retained names cannot carry custom credentials in a valid SDK config;
    a non-SDK client must not put secrets in them. Header names alone cannot
    identify credentials deliberately sent under these reserved names.
    It does not carry the body — the simulator replays captured shapes and
    does not evaluate request content (#189/#250)."""

    method: str
    path: str
    headers: Mapping[str, str]


def _record(scope: MutableMapping[str, Any], *, cfg: DonkeyConfig | None = None) -> RecordedRequest:
    """Build a request record with only explicitly allowlisted header values."""
    raw: Any = scope.get("headers") or []
    sensitive = (
        {
            name.lower()
            for name in (cfg.llm_proxy_key_header, cfg.user_token_header)
            if name is not None
        }
        if cfg is not None
        else set()
    )
    headers: dict[str, str] = {}
    for name, value in raw:
        key = name.decode("latin-1")
        headers[key] = (
            value.decode("latin-1")
            if key.lower() in _RECORDED_HEADERS and key.lower() not in sensitive
            else _REDACTED
        )
    return RecordedRequest(
        method=str(scope.get("method", "")),
        path=str(scope.get("path", "")),
        headers=headers,
    )


class _RecordingApp:
    """An ASGI app that records every HTTP request, then delegates to a swappable
    inner app. The inner app is swapped (never the outer one uvicorn holds) so
    :meth:`Gateway.set_scenarios` can reconfigure the simulator live, without
    restarting the server or losing the request log."""

    def __init__(self, app: ASGIApp, cfg: DonkeyConfig | None = None) -> None:
        self.app = app
        self.cfg = cfg
        self.requests: list[RecordedRequest] = []

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") == "http":
            self.requests.append(_record(scope, cfg=self.cfg))
        await self.app(scope, receive, send)


class Gateway:
    """A running out-of-process simulator plus the spy over what it received.

    Yielded by the ``gateway`` pytest fixture. ``.url`` is a real
    ``http://127.0.0.1:<port>`` any process can point ``DONKEY_LLM_PROXY_URL`` at;
    the simulator replays the same captured fixtures the SDK's typed refusals are
    tested against, and every response carries ``x-donkey-simulator: true`` (#189).
    """

    def __init__(self, *, url: str, recorder: _RecordingApp, shutdown: Callable[[], None]) -> None:
        self._url = url
        self._recorder = recorder
        self._shutdown = shutdown

    @property
    def url(self) -> str:
        """The base URL of the running simulator, e.g. ``http://127.0.0.1:54123``."""
        return self._url

    @property
    def requests_received(self) -> int:
        """How many requests the gateway has received since start (or the last
        :meth:`reset`)."""
        return len(self._recorder.requests)

    @property
    def requests(self) -> list[RecordedRequest]:
        """A snapshot of the recorded requests, oldest first (secret redacted)."""
        return list(self._recorder.requests)

    def set_scenarios(self, *scenarios: str | FaultScenario) -> None:
        """Reconfigure the simulator's #188 fault-injection scenarios for this
        test, live (no restart). Each argument is either a parsed
        :class:`~donkey_kit.simulator.scenarios.FaultScenario` or a ``--scenario`` spec
        string (e.g. ``"pii_block:every=1"``). Scenarios are stateful and
        single-use, so this builds a fresh simulator each call; the request log is
        left untouched (use :meth:`reset` to clear it)."""
        parsed = tuple(parse_scenario(s) if isinstance(s, str) else s for s in scenarios)
        self.configure(SimulatorConfig(scenarios=parsed))

    def configure(
        self, config: SimulatorConfig | None = None, *, recording_config: DonkeyConfig | None = None
    ) -> None:
        """Swap in a fresh simulator built from ``config`` (the defaults when
        ``None``), live: same port, same request log. :meth:`set_scenarios` is the
        scenarios-only shorthand; this also takes the budget-window tunables.
        ``recording_config`` identifies custom key/user-token headers whose
        values must be redacted even when their names are allowlisted. Omitting
        it retains the recorder's current config."""
        if recording_config is not None:
            self._recorder.cfg = recording_config
        self._recorder.app = build_app(config)

    def reset(self) -> None:
        """Clear the recorded-request log (the running server is left alone)."""
        self._recorder.requests.clear()

    def close(self) -> None:
        """Stop the server and join its thread. Idempotent; the fixture calls it
        on teardown, including when the test failed."""
        self._shutdown()


def _bound_port(server: Any) -> int:
    """Read the ephemeral port uvicorn actually bound. Valid only once
    ``server.started`` is true (that is when ``server.servers`` is populated),
    which is what makes ``port=0`` race-free — we never guess a port."""
    for srv in getattr(server, "servers", None) or []:
        for sock in getattr(srv, "sockets", None) or []:
            return int(sock.getsockname()[1])
    raise RuntimeError("could not determine the local gateway simulator's bound port")


def start_gateway(
    config: SimulatorConfig | None = None,
    *,
    host: str = "127.0.0.1",
    startup_timeout: float = _STARTUP_TIMEOUT_S,
    recording_config: DonkeyConfig | None = None,
) -> Gateway:
    """Boot the local gateway simulator on an ephemeral port and return a
    :class:`Gateway` handle. The caller owns teardown via :meth:`Gateway.close`.
    Pass ``recording_config`` when custom key/user-token header names are used;
    their values are redacted even if those names are normally allowlisted.

    Raises :class:`ImportError` (naming ``pip install "donkey-kit[local]"``) when
    the ``[local]`` extra — ``starlette`` + ``uvicorn`` — is not installed.
    """
    try:
        import uvicorn  # lazy: provided by the [local] extra
    except ImportError as exc:
        raise ImportError(_LOCAL_EXTRA_HINT) from exc

    try:
        # build_app imports starlette lazily; a missing [local] surfaces here too.
        recorder = _RecordingApp(build_app(config), recording_config)
    except ImportError as exc:
        raise ImportError(_LOCAL_EXTRA_HINT) from exc

    uv_config = uvicorn.Config(recorder, host=host, port=0, log_level="warning")
    server = uvicorn.Server(uv_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + startup_timeout
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5.0)
        raise RuntimeError(f"local gateway simulator did not start within {startup_timeout}s")

    port = _bound_port(server)

    def _shutdown() -> None:
        server.should_exit = True
        thread.join(timeout=10.0)

    return Gateway(url=f"http://{host}:{port}", recorder=recorder, shutdown=_shutdown)

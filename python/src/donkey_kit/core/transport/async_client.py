"""The governed async client, the one every adapter is handed (BG §1.1)."""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import TYPE_CHECKING, Any

import httpx

from ..auth import AuthProvider
from ..budget import Budget
from ..config import DonkeyConfig
from ..errors import PolicyViolation
from ..telemetry import GenAiSpan, genai_span, start_genai_span
from .failures import _no_attempts
from .governed import GovernedTransport, _AsyncMountRouter, _LoopLocalTransport
from .headers import Origin, _apply_auth, _apply_call_id_header
from .observe import _log_target, _observe_final
from .pipeline import AsyncClientOptions, _GovernedPipeline
from .policy import Finish, Retry, _request_model
from .streaming import _aread_error_body, _is_streaming_success, _SpanClosingAsyncStream
from .views import DonkeyAsyncClientView

if TYPE_CHECKING:
    from httpx._client import UseClientDefault
    from httpx._types import AuthTypes
    from typing_extensions import Unpack

__all__ = ["DonkeyAsyncClient", "build_http_client"]

_log = logging.getLogger(__name__)


class DonkeyAsyncClient(_GovernedPipeline, httpx.AsyncClient):
    """An ``httpx.AsyncClient`` that injects attribution/correlation/auth headers
    and applies the SDK's retry policy. Every adapter that accepts a custom HTTP
    client MUST be given one of these.

    A client serves exactly one credential plane (BG §1.1). The default is the
    data plane (the LLM proxy): ``auth`` is the data-plane credential, which is
    the model-wallet JWT provider in ``jwt`` mode, the bearer-token provider in
    ``bearer`` mode, and ``None`` in client-id mode.
    ``control_plane=True`` marks a client for Anypoint platform calls: ``auth``
    is then the connected-app provider, and the ``jwt``-mode wallet headers are
    never stamped on its requests.

    Credentials go only to the plane's checked endpoints (see
    :class:`_CheckedEndpoints`); ``origins`` shares that set with another client.
    Redirects are not followed unless a caller asks for it per request.

    Every request goes through :attr:`governed_transport`, whose
    :meth:`~GovernedTransport.replace_inner` swaps the transport underneath on a
    live client (#728)."""

    def __init__(
        self,
        cfg: DonkeyConfig,
        auth: AuthProvider | None,
        *,
        budget: Budget | None = None,
        control_plane: bool = False,
        origins: set[Origin] | None = None,
        **kw: Unpack[AsyncClientOptions],
    ) -> None:
        self._init_pipeline(cfg, budget=budget, control_plane=control_plane, origins=origins)
        # NB: httpx.AsyncClient uses ``self._auth`` internally, so we must NOT
        # store our token provider there — super().__init__() would clobber it.
        self._token_provider = auth
        self._view: DonkeyAsyncClientView | None = None
        super().__init__(
            timeout=cfg.timeout_s, event_hooks={"request": [self._inject_headers]}, **kw
        )
        self._pool_per_loop(kw)
        # Fold env/``proxy=``/``mounts=`` mounts into the base transport so the
        # governed transport below covers every route (#801).
        if self._mounts:
            self._transport = _AsyncMountRouter(self._transport, self._mounts.items())
            self._mounts = {}
        self._governed = GovernedTransport(self._transport, lambda: self._mounts)
        self._transport = self._governed

    def _pool_per_loop(self, kw: AsyncClientOptions) -> None:
        """Give each event loop its own connection pool (#807). Wraps every pool
        httpx built here: the default transport and the env-proxy mounts. A
        later loop's pool comes from an httpx client built with the same
        ``kw``. A ``transport`` or ``mounts`` the caller passed is theirs, so it
        is left as given."""

        def fresh() -> httpx.AsyncClient:
            return httpx.AsyncClient(**kw)

        # httpx exposes no public accessor for the transport/mounts a client
        # built from ``kw``; reading its private ``_transport``/``_mounts`` is the
        # only way to reuse httpx's own construction (the SLF001 exemptions here).
        if "transport" not in kw:
            self._transport = _LoopLocalTransport(
                self._transport,
                lambda: fresh()._transport,  # noqa: SLF001
            )
        if "mounts" not in kw:

            def build_mount(pattern: Any) -> httpx.AsyncBaseTransport:  # noqa: ANN401 - httpx-private key
                client = fresh()
                return client._mounts.get(pattern) or client._transport  # noqa: SLF001

            self._mounts = {
                pattern: None
                if mount is None
                else _LoopLocalTransport(mount, functools.partial(build_mount, pattern))
                for pattern, mount in self._mounts.items()
            }

    @property
    def governed_transport(self) -> GovernedTransport:
        """The transport every request goes through. ``simulate()`` (#190) and
        the conformance harness (#191) put a fixture under the governed pipeline
        with its :meth:`~GovernedTransport.replace_inner`."""
        return self._governed

    def view(self) -> DonkeyAsyncClientView:
        """The non-owning view of this client, the one to hand a framework (#733).
        It sends through this client, and closing it leaves this client open."""
        if self._view is None:
            self._view = DonkeyAsyncClientView(self)
        return self._view

    @property
    def token_provider(self) -> AuthProvider | None:
        """The attached :class:`AuthProvider`, if any. Read by ``LLMClient`` to
        tell an attached JWT provider from a missing one in ``jwt`` auth mode
        (#509) — the provider lives on the transport, not on ``DonkeyConfig``."""
        return self._token_provider

    async def _inject_headers(self, request: httpx.Request) -> None:
        if not self._stamp(request):
            return
        provider = self._token_provider
        token = await provider.token() if provider is not None else None
        _apply_auth(self._cfg, request, token, control_plane=self._control_plane)

    # --- lifecycle hooks (BG §1.1) ------------------------------------------
    # The *logical* per-``send()`` seams, distinct from the per-wire
    # ``_inject_headers`` event hook. Internal (underscore-prefixed) extension
    # points for the SDK's own layers, NOT public API. Subclasses/layers
    # override; do not call these directly.

    async def _on_response(self, request: httpx.Request, response: httpx.Response) -> None:
        """Called once with the final response returned to the caller (after
        retries and any 401 refresh settle). Feeds the attached :class:`Budget`
        (#185) and records a governed model call into ``donkey.last_call``
        (#362). A subclass that overrides this hook must call
        ``super()._on_response(...)`` to keep budget and last-call tracking."""
        _observe_final(request, response, self._budget)

    async def _on_refusal(self, violation: PolicyViolation) -> None:
        """Called once when the final response is a policy refusal, with the
        typed violation :func:`~donkey_kit.core.errors.classify` made of it,
        after the span is recorded and before the response is returned. The
        attachment point for the declarative reaction handlers (#208). The
        default does nothing: the refusal is still returned as the response."""

    async def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        auth: AuthTypes | UseClientDefault | None = httpx.USE_CLIENT_DEFAULT,
        follow_redirects: bool | UseClientDefault = httpx.USE_CLIENT_DEFAULT,
    ) -> httpx.Response:
        """Send ``request`` with the governed headers, retry policy and GenAI span.

        Retries a retryable status (not one the gateway already failed over) up to
        ``max_retries`` times, and re-sends once with a fresh token after a 401.
        A refusal is returned as the response, not raised here.

        Raises:
            GatewayUnavailable: The gateway could not be reached (DNS, refused
                connection, TLS, timeout).
        """
        model = _request_model(request)
        # A GenAI span is opened only for a model call (a JSON body carrying a
        # ``model``); GETs, token fetches and bodyless POSTs open none and stay
        # byte-identical (#192).
        enabled = self._cfg.telemetry and model is not None
        capture = self._cfg.telemetry_capture_content
        options = (stream, auth, follow_redirects)
        # ``stream=True`` is the streaming path (#193): the usage lands in the
        # terminal SSE event, read by the caller long after send() returns, so
        # the span is detached and ended by the stream wrapper in ``_finish``. A
        # model stream takes this path even with telemetry off — the span is then
        # inert, but the wrapper still scans the terminal event into
        # ``donkey.last_call`` (#817).
        if model is not None and stream:
            gspan = start_genai_span(enabled=enabled, capture_content=capture)
            try:
                gspan.record(request_model=model)
                return await self._send_with_retries(request, gspan, options)
            except BaseException:
                # No response settled (a transport error or cancellation), or a
                # raise after it did: no stream wrapper will close the detached
                # span, so mark it failed and end it here (#193, AC #4).
                gspan.set_error()
                gspan.end()
                raise
        # Buffered path: the span is a context manager, so it closes even when
        # the send raises before a response exists (#179/#192).
        with genai_span(enabled=enabled, capture_content=capture) as gspan:
            gspan.record(request_model=model)
            return await self._send_with_retries(request, gspan, options)

    async def _send_with_retries(
        self,
        request: httpx.Request,
        gspan: GenAiSpan,
        options: tuple[bool, AuthTypes | UseClientDefault | None, bool | UseClientDefault],
    ) -> httpx.Response:
        """The retry / 401-refresh loop, shared by the buffered and streaming
        paths; :func:`~.policy.decide_retry` makes every decision."""
        # Pin the per-call id ONCE, before the loop, so it is stable across
        # retries and the 401 refresh (BG §1.1, #195). The run correlation id is set
        # per-send by the event hook (stable per request, so idempotent, #803).
        _apply_call_id_header(request, self._call_id_header)
        attempts = self._cfg.max_retries + 1
        if attempts < 1:
            raise _no_attempts(self._cfg.max_retries)
        stream, auth, follow_redirects = options
        refreshed_once = False
        attempt = 0
        while True:
            try:
                response = await super().send(
                    request, stream=stream, auth=auth, follow_redirects=follow_redirects
                )
            except (httpx.TransportError, RuntimeError) as exc:
                typed = self._send_error(request, exc, client_closed=self.is_closed)
                if typed is None:
                    raise
                raise typed from exc
            provider = self._token_provider
            decision = self._decide(
                request,
                response,
                attempt=attempt,
                attempts=attempts,
                can_refresh=provider is not None and not refreshed_once,
            )
            if isinstance(decision, Finish):
                return await self._finish(request, response, gspan, streaming=stream)
            await response.aclose()
            if isinstance(decision, Retry):
                await asyncio.sleep(decision.delay)
                attempt += 1
                continue
            # Refresh: an auth re-send, not a backoff. It does not consume the
            # retry budget, and the event hook re-runs on send() for a fresh token.
            assert provider is not None  # Refresh only when can_refresh
            refreshed_once = True
            _log.debug(
                "%s returned 401; refreshing the token and re-sending once", _log_target(request)
            )
            await provider.invalidate()

    async def _finish(
        self,
        request: httpx.Request,
        response: httpx.Response,
        gspan: GenAiSpan,
        *,
        streaming: bool,
    ) -> httpx.Response:
        """Fire the response hooks exactly once, on the final response returned
        to the caller, then record the response attributes on the GenAI span.
        Retry/refresh branches close their intermediate response and loop instead
        of coming here, so the hooks never see a closed response.

        A non-2xx stream body is read first (bounded, #805) so the refusal is
        classified exactly as a buffered one; a transport failure mid-read
        surfaces as :class:`GatewayUnavailable`. A refusal then fires
        ``_on_refusal`` (#208). A caller who opted into
        ``on_model_substitution="raise"`` gets the error instead of the response,
        after telemetry, so the substituted call is still on the span (#309).

        Streaming path (#193): the span is detached, so it is ended HERE. A
        streamed 2xx SSE body is handed to a :class:`_SpanClosingAsyncStream` that
        fills ``gen_ai.usage.*`` and ends the span when the stream closes — on
        drain, mid-iteration abandonment, or exception. Anything else on a stream
        request has no SSE body to scan, so the span is ended inline."""
        if streaming and response.status_code // 100 != 2:
            try:
                await _aread_error_body(response)
            except httpx.TransportError as exc:
                await response.aclose()
                raise self._unreachable(request, exc) from exc
        await self._on_response(request, response)
        violation, substitution = self._settle(request, response, gspan)
        if violation is not None:
            await self._on_refusal(violation)
        if substitution is not None:
            await response.aclose()  # so an aborted stream leaks no connection
            raise substitution
        if streaming:
            body = response.stream
            if _is_streaming_success(response) and isinstance(body, httpx.AsyncByteStream):
                response.stream = _SpanClosingAsyncStream(body, gspan)
            else:
                gspan.end()
        return response


def build_http_client(
    cfg: DonkeyConfig,
    auth: AuthProvider | None,
    *,
    budget: Budget | None = None,
    control_plane: bool = False,
) -> DonkeyAsyncClient:
    """Factory for a shared client, one per credential plane (BG §1.1). Pass
    ``budget`` to track the in-band token window on every response (BG §1.3,
    #185); omit it on the control plane, which observes no budget. Pass
    ``control_plane=True`` for a client that calls the Anypoint platform (see
    :class:`DonkeyAsyncClient`)."""
    return DonkeyAsyncClient(cfg, auth, budget=budget, control_plane=control_plane)

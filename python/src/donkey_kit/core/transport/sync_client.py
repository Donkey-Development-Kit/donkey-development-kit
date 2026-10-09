"""The governed blocking client, for ``donkey.llm.client(sync=True)`` (BG §1.1)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx

from ..budget import Budget
from ..config import TOKEN_AUTH_MODES, DonkeyConfig
from ..errors import PolicyViolation
from ..telemetry import GenAiSpan, genai_span, start_genai_span
from .failures import _no_attempts, sync_token_auth_error
from .governed import GovernedSyncTransport, _SyncMountRouter
from .headers import Origin, _apply_auth, _apply_call_id_header
from .observe import _observe_final
from .pipeline import ClientOptions, _GovernedPipeline
from .policy import Finish, Retry, _request_model
from .streaming import _is_streaming_success, _read_error_body, _SpanClosingSyncStream
from .views import DonkeyClientView

if TYPE_CHECKING:
    from httpx._client import UseClientDefault
    from httpx._types import AuthTypes
    from typing_extensions import Unpack

__all__ = ["DonkeyClient", "build_sync_http_client"]


class DonkeyClient(_GovernedPipeline, httpx.Client):
    """The blocking twin of :class:`DonkeyAsyncClient`, for ``donkey.llm.client(
    sync=True)``.

    It injects the same correlation/attribution headers and applies the same
    retry policy, through the same shared pipeline, so a synchronous caller is
    governed identically to an async one. Without it, a sync caller would fall
    back to whatever bare client the framework builds for itself and quietly
    lose both.

    It takes **no** :class:`AuthProvider`: that protocol is async-only
    (``async def token()``), and there is no correct way to await it from here.
    That costs nothing on the LLM data plane, which authenticates with the
    ``client_id``/``client_secret`` header pair (docs/verified-apis.md §2/§3)
    rather than a fetched token. It does mean the control-plane surfaces —
    ``registry`` and ``tools`` — stay async-only; see BG §1.1 for why the two
    credentials are deliberately not conflated. With no provider there is
    nothing to refresh, so a 401 here is a real credential failure and terminal.

    In the token auth modes (``llm_proxy_auth='jwt'`` or ``'bearer'``) the
    credential is exactly such a fetched token, so every request raises
    :func:`sync_token_auth_error` instead of going out unauthenticated (#509,
    #736, #836). The check sits in :meth:`build_request`, which the OpenAI SDK
    calls outside the ``try`` that turns transport errors into
    ``APIConnectionError``, so a framework's sync call (``ChatOpenAI.invoke()``)
    raises the ``ConfigError`` itself.

    Like its async twin it sends credentials only to the checked endpoints; pass
    the async client's :attr:`checked_origins` as ``origins`` to share them.
    """

    def __init__(
        self,
        cfg: DonkeyConfig,
        *,
        budget: Budget | None = None,
        origins: set[Origin] | None = None,
        **kw: Unpack[ClientOptions],
    ) -> None:
        self._init_pipeline(cfg, budget=budget, control_plane=False, origins=origins)
        self._view: DonkeyClientView | None = None
        super().__init__(
            timeout=cfg.timeout_s, event_hooks={"request": [self._inject_headers]}, **kw
        )
        if self._mounts:  # see DonkeyAsyncClient.__init__ (#801)
            self._transport = _SyncMountRouter(self._transport, self._mounts.items())
            self._mounts = {}
        self._governed = GovernedSyncTransport(self._transport, lambda: self._mounts)
        self._transport = self._governed

    def build_request(self, *args: Any, **kwargs: Any) -> httpx.Request:
        """Build a request, refusing in a token auth mode.

        Raises:
            ConfigError: ``llm_proxy_auth`` is ``jwt`` or ``bearer``, whose token
                providers are async-only.
        """
        if self._cfg.llm_proxy_auth in TOKEN_AUTH_MODES:
            raise sync_token_auth_error(self._cfg.llm_proxy_auth)
        return super().build_request(*args, **kwargs)

    @property
    def governed_transport(self) -> GovernedSyncTransport:
        """The transport every request goes through (see
        :attr:`DonkeyAsyncClient.governed_transport`)."""
        return self._governed

    def view(self) -> DonkeyClientView:
        """The non-owning view of this client (see :meth:`DonkeyAsyncClient.view`)."""
        if self._view is None:
            self._view = DonkeyClientView(self)
        return self._view

    def _inject_headers(self, request: httpx.Request) -> None:
        if self._stamp(request):
            _apply_auth(self._cfg, request, None, control_plane=False)

    # --- lifecycle hooks (BG §1.1) ------------------------------------------
    # Blocking twins of the async seams; internal, not public API.

    def _on_response(self, request: httpx.Request, response: httpx.Response) -> None:
        """Called once with the final response returned to the caller (see
        :meth:`DonkeyAsyncClient._on_response`). A subclass that overrides this
        must call ``super()._on_response(...)``."""
        _observe_final(request, response, self._budget)

    def _on_refusal(self, violation: PolicyViolation) -> None:
        """Called once for a final policy refusal (see
        :meth:`DonkeyAsyncClient._on_refusal`, #208). The default does nothing."""

    def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        auth: AuthTypes | UseClientDefault | None = httpx.USE_CLIENT_DEFAULT,
        follow_redirects: bool | UseClientDefault = httpx.USE_CLIENT_DEFAULT,
    ) -> httpx.Response:
        """The blocking twin of :meth:`DonkeyAsyncClient.send`: same headers, retries and span.

        Raises:
            ConfigError: ``llm_proxy_auth`` is ``jwt`` or ``bearer``. A request
                that did not come from :meth:`build_request` (the core ``httpx2``
                bridge builds its own) is refused here too, so no path sends it
                unauthenticated (#509, #728).
            GatewayUnavailable: The gateway could not be reached.
        """
        if self._cfg.llm_proxy_auth in TOKEN_AUTH_MODES:
            raise sync_token_auth_error(self._cfg.llm_proxy_auth)
        model = _request_model(request)
        enabled = self._cfg.telemetry and model is not None  # see DonkeyAsyncClient.send
        capture = self._cfg.telemetry_capture_content
        options = (stream, auth, follow_redirects)
        if model is not None and stream:
            # Streaming: a detached span the stream wrapper ends, inert with
            # telemetry off (see DonkeyAsyncClient.send, #193, #817).
            gspan = start_genai_span(enabled=enabled, capture_content=capture)
            try:
                gspan.record(request_model=model)
                return self._send_with_retries(request, gspan, options)
            except BaseException:
                gspan.set_error()
                gspan.end()
                raise
        with genai_span(enabled=enabled, capture_content=capture) as gspan:
            gspan.record(request_model=model)
            return self._send_with_retries(request, gspan, options)

    def _send_with_retries(
        self,
        request: httpx.Request,
        gspan: GenAiSpan,
        options: tuple[bool, AuthTypes | UseClientDefault | None, bool | UseClientDefault],
    ) -> httpx.Response:
        """The retry loop (see :meth:`DonkeyAsyncClient._send_with_retries`)."""
        _apply_call_id_header(request, self._call_id_header)  # once, before the loop (#195)
        attempts = self._cfg.max_retries + 1
        if attempts < 1:
            raise _no_attempts(self._cfg.max_retries)
        stream, auth, follow_redirects = options
        attempt = 0
        while True:
            try:
                response = super().send(
                    request, stream=stream, auth=auth, follow_redirects=follow_redirects
                )
            except (httpx.TransportError, RuntimeError) as exc:
                typed = self._send_error(request, exc, client_closed=self.is_closed)
                if typed is None:
                    raise
                raise typed from exc
            decision = self._decide(
                request, response, attempt=attempt, attempts=attempts, can_refresh=False
            )
            if isinstance(decision, Finish):
                return self._finish(request, response, gspan, streaming=stream)
            response.close()
            assert isinstance(decision, Retry)  # can_refresh=False: never a Refresh
            time.sleep(decision.delay)
            attempt += 1

    def _finish(
        self,
        request: httpx.Request,
        response: httpx.Response,
        gspan: GenAiSpan,
        *,
        streaming: bool,
    ) -> httpx.Response:
        """Fire the response hooks exactly once, on the response actually
        returned, then record the span (see :meth:`DonkeyAsyncClient._finish`,
        including the bounded read of a non-2xx stream body, #805)."""
        if streaming and response.status_code // 100 != 2:
            try:
                _read_error_body(response)
            except httpx.TransportError as exc:
                response.close()
                raise self._unreachable(request, exc) from exc
        self._on_response(request, response)
        violation, substitution = self._settle(request, response, gspan)
        if violation is not None:
            self._on_refusal(violation)
        if substitution is not None:
            response.close()
            raise substitution
        if streaming:
            body = response.stream
            if _is_streaming_success(response) and isinstance(body, httpx.SyncByteStream):
                response.stream = _SpanClosingSyncStream(body, gspan)
            else:
                gspan.end()
        return response


def build_sync_http_client(
    cfg: DonkeyConfig,
    *,
    budget: Budget | None = None,
    origins: set[Origin] | None = None,
) -> DonkeyClient:
    """Factory for the shared blocking client (BG §1.1). See :class:`DonkeyClient`
    for why it takes no :class:`AuthProvider`. Pass ``budget`` to share one budget
    object with the async client (BG §1.3, #185), and ``origins`` to share its
    checked endpoints."""
    return DonkeyClient(cfg, budget=budget, origins=origins)

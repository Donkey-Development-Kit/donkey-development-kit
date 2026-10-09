"""The typed errors a governed send raises instead of an httpx failure (#728).

A send that gets no response (the gateway was unreachable), a send on a closed
client or loop, a retry loop configured to send nothing, and a blocking call in
an async-only auth mode. Each one is built here, from the request and config,
so the two clients raise identical errors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from ..config import TOKEN_AUTH_MODES, DonkeyConfig, LlmProxyAuth, missing_llm_auth_error
from ..errors import ConfigError, GatewayUnavailable, gateway_unavailable

if TYPE_CHECKING:
    from .async_client import DonkeyAsyncClient

__all__ = ["checked_llm_config", "sync_token_auth_error"]


def _no_attempts(max_retries: object) -> ConfigError:
    """The error for a retry loop that sent nothing: ``max_retries`` is below 0.
    ``DonkeyConfig`` refuses that value, so only a config altered after
    construction gets here (#809)."""
    return ConfigError(
        f"max_retries is {max_retries!r}, so no request was sent; expected a whole "
        "number, 0 or more."
    )


def _gateway_unavailable(
    request: httpx.Request,
    exc: httpx.TransportError,
    *,
    correlation_header: str,
    call_id_header: str,
) -> GatewayUnavailable:
    """Wrap a transport-level httpx failure (DNS, refused connection, TLS error,
    timeout — no HTTP response) as the typed :class:`GatewayUnavailable` (#379,
    BG §1.2). ``httpx.TransportError`` is the precise base: it covers exactly the
    "never got a response" family and excludes response-bearing failures
    (``HTTPStatusError``), so a 4xx/5xx still flows to :func:`classify` unchanged.

    The origin that failed is put on the exception (the request URL is the
    truthful target, honouring any proxy/base-url override). The run/call ids the
    client already stamped on the request are carried so an availability failure
    quotes the same ids a response error would — even though the upstream
    provider's ``request_id`` (read from a response) is necessarily absent."""
    url = request.url
    host = url.host if url.port is None else f"{url.host}:{url.port}"
    return gateway_unavailable(
        base_url=f"{url.scheme}://{host}" if host else None,
        cause=exc,
        correlation_id=request.headers.get(correlation_header),
        call_id=request.headers.get(call_id_header),
    )


def _lifecycle_error(
    request: httpx.Request,
    exc: RuntimeError,
    *,
    client_closed: bool,
    correlation_header: str,
    call_id_header: str,
) -> ConfigError | None:
    """Type the two lifecycle ``RuntimeError``s a send can hit (#813), or return
    ``None`` to let any other ``RuntimeError`` through unchanged.

    * The client is closed: httpx refuses the send. Detected from the client's
      own state, not httpx's message wording.
    * The event loop is closed: a pooled connection belongs to a loop that has
      since closed. The SDK's own pools are per loop (#807), so this comes from
      a transport the caller supplied, reused across ``asyncio.run()`` calls."""
    if client_closed:
        message = (
            "Cannot send: this Donkey's HTTP client is closed. It was closed by "
            "Donkey.aclose()/close() or by a framework that closed the client it "
            "was given."
        )
        remediation = (
            "Make the call on an open client: create a new Donkey, or keep the "
            "Donkey open (do not close it, or the client it hands a framework, "
            "until its last call)."
        )
    elif str(exc) == "Event loop is closed":
        message = (
            "Cannot send: the async client's connections belong to an event loop "
            "that has closed (for example, a transport passed in and reused across "
            "asyncio.run() calls)."
        )
        remediation = (
            "Build the transport you pass in inside the asyncio.run() that uses "
            "it, or make every call from the same loop."
        )
    else:
        return None
    return ConfigError(
        message,
        remediation=remediation,
        correlation_id=request.headers.get(correlation_header),
        call_id=request.headers.get(call_id_header),
    )


def sync_token_auth_error(mode: LlmProxyAuth) -> ConfigError:
    """The error for a blocking call in a token auth mode (jwt / model-wallet or
    bearer), shared by ``donkey.llm.client(sync=True)`` and every sync call
    through :class:`DonkeyClient` (#509, #736, #836), so both surfaces fail the
    same way."""
    label = "JWT / model-wallet" if mode == "jwt" else "Bearer-token"
    token = "JWT" if mode == "jwt" else "bearer token"
    return ConfigError(
        f"{label} auth mode (llm_proxy_auth={mode!r}) is async-only: "
        f"the credential is a rotating {token} fetched from an async AuthProvider, "
        "and the blocking client cannot await it. Use the async surface — "
        "`donkey.llm.client()` / `donkey.openai()` without sync=True, or a "
        "framework's async call (`ainvoke()` / `astream()`, not `invoke()`) — or "
        "switch to client-id auth for a synchronous caller."
    )


def checked_llm_config(
    cfg: DonkeyConfig, http: DonkeyAsyncClient, *, sync: bool = False
) -> DonkeyConfig:
    """``cfg`` validated for a data-plane LLM call over ``http``, the shared client.

    The one guard behind every governed LLM surface: ``donkey.llm.client()``
    and each framework adapter's ``connection_kwargs()`` (#726). In a token auth
    mode (jwt or bearer) it also checks the two things config alone cannot (#509,
    #828, #836): a ``sync`` caller is refused with :func:`sync_token_auth_error`,
    since the blocking client cannot await the token; and a shared client with
    no ``AuthProvider`` is refused with ``missing_llm_auth_error``, since the
    token rides only that provider and every call would otherwise 401.
    """
    checked = cfg.validated(need="llm")
    mode = checked.llm_proxy_auth
    if mode in TOKEN_AUTH_MODES:
        if sync:
            raise sync_token_auth_error(mode)
        if http.token_provider is None:
            raise missing_llm_auth_error(mode)
    return checked

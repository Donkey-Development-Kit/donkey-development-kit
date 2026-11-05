"""Governed outbound A2A, using the official SDK's wire transports (BG §2.9)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING

import httpx

from ..core import _verify
from ..core.errors import ConfigError, DonkeyError, RegistryError, ToolInvocationError, classify
from ..core.identity import current_user, user_token_header
from ..core.inbound import outbound_call
from ..core.step_up import GatewayAuthChallenge, GatewayAuthRequired
from ..registry.criteria import GovernanceCriteria
from ..registry.models import AssetRef
from ..tools.auth import McpAuth, check_gateway_endpoint, gateway_auth

if TYPE_CHECKING:
    from a2a.client.transports.base import ClientTransport
    from a2a.types import SendMessageRequest, SendMessageResponse, StreamResponse

    from ..core.auth import AuthProvider
    from ..core.config import DonkeyConfig
    from ..core.transport import DonkeyAsyncClient
    from ..registry.exchange import ExchangeRegistry

__all__ = ["A2AFacade"]


def _check_sdk_telemetry() -> None:
    """Require known-disabled SDK instrumentation, not a sampling assumption."""
    try:
        from a2a.utils import telemetry
    except ImportError:
        telemetry = None
    # a2a-sdk 1.2.2 binds tracing at import time; later env changes do not disable it.
    installed = getattr(telemetry, "otel_installed", None)
    enabled = getattr(telemetry, "otel_enabled", None)
    if installed is False or (installed is True and enabled is False):
        return
    raise ConfigError(
        "Outbound A2A calls require disabled SDK tracing or explicit permission. "
        "The imported A2A SDK tracing state is enabled or cannot be determined.",
        remediation=(
            "Set OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false before importing a2a "
            "and restart the process, or set allow_a2a_sdk_telemetry=True "
            "(DONKEY_ALLOW_A2A_SDK_TELEMETRY=true) to accept that SDK tracing may "
            "export sensitive diagnostics. DDK's telemetry setting is independent."
        ),
    )


def _auth_challenge(
    result: SendMessageResponse | StreamResponse,
    transport: ClientTransport,
    responses: list[httpx.Response],
) -> None:
    from a2a.compat.v0_3.jsonrpc_transport import CompatJsonRpcTransport
    from a2a.types import TaskState

    # #1393 captured this policy only on v0.3. SDK conversion is not v1 wire evidence.
    if not isinstance(transport, CompatJsonRpcTransport) or result.WhichOneof("payload") != "task":
        return
    task = result.task
    if task.status.state != TaskState.TASK_STATE_AUTH_REQUIRED:
        return
    for part in task.status.message.parts:
        if part.WhichOneof("content") != "data":
            continue
        data = part.data.struct_value
        if "authChallenge" not in data.fields:
            continue
        value = data.fields["authChallenge"]
        if value.WhichOneof("kind") != "struct_value":
            continue
        fields = value.struct_value.fields
        keys = (
            "secondaryAuthProvider",
            "authorizationEndpoint",
            "tokenEndpoint",
            "audience",
            "redirectUri",
            "responseType",
            "codeChallengeMethod",
            "bodyEncoding",
        )
        if any(
            key not in fields or fields[key].WhichOneof("kind") != "string_value" for key in keys
        ):
            continue
        if "scopes" not in fields or fields["scopes"].WhichOneof("kind") != "list_value":
            continue
        scopes = fields["scopes"].list_value.values
        if any(scope.WhichOneof("kind") != "string_value" for scope in scopes):
            continue
        challenge = GatewayAuthChallenge(
            secondary_auth_provider=fields["secondaryAuthProvider"].string_value,
            authorization_endpoint=fields["authorizationEndpoint"].string_value,
            token_endpoint=fields["tokenEndpoint"].string_value,
            scopes=tuple(scope.string_value for scope in scopes),
            audience=fields["audience"].string_value,
            redirect_uri=fields["redirectUri"].string_value,
            response_type=fields["responseType"].string_value,
            code_challenge_method=fields["codeChallengeMethod"].string_value,
            body_encoding=fields["bodyEncoding"].string_value,
        )
        response = responses[-1] if responses else None
        context = classify(response) if response is not None else None
        raise GatewayAuthRequired(
            "The A2A task requires additional authorization.",
            challenge=challenge,
            task_id=task.id,
            context_id=task.context_id,
            response=response,
            correlation_id=context.correlation_id if context is not None else None,
            call_id=context.call_id if context is not None else None,
            request_id=context.request_id if context is not None else None,
        ) from None


async def _cleanup(closers: Sequence[Callable[[], Awaitable[None]]]) -> None:
    active = sys.exc_info()[1]
    failure: BaseException | None = None
    for close in closers:
        try:
            await close()
        except BaseException as exc:  # noqa: BLE001 - defer, never discard, cleanup-only failures
            # Every owned resource gets a close attempt, even after cancellation.
            if failure is None or not isinstance(exc, Exception):
                failure = exc
    if active is None and failure is not None:
        if not isinstance(failure, Exception):
            raise failure
        raise ToolInvocationError(
            "The A2A call could not close its local resources.",
            remediation="Check the local transport lifecycle and retry with a healthy connection.",
        ) from None


@contextmanager
def _protocol_errors(responses: list[httpx.Response]) -> Iterator[None]:
    """Keep SDK parsing/protocol diagnostics out of rendered errors."""
    from a2a.utils.errors import A2AError

    # The SDK's protobuf parser ships without typing stubs.
    from google.protobuf.json_format import Error  # type: ignore[import-untyped]
    from pydantic import ValidationError

    try:
        yield
    except DonkeyError:
        raise
    except (
        A2AError,
        Error,
        ValidationError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
    ) as exc:
        # SDK stream cleanup can replace cancellation or a governed failure.
        context_error: BaseException | None = exc
        while context_error is not None:
            if isinstance(context_error, (asyncio.CancelledError, DonkeyError)):
                raise context_error from None
            context_error = context_error.__context__
        response = responses[-1] if responses else None
        context = classify(response) if response is not None else None
        raise ToolInvocationError(
            "The A2A agent returned an invalid response or a protocol error.",
            remediation="Check the agent's A2A protocol and request types; inspect server logs.",
            response=response,
            correlation_id=context.correlation_id if context is not None else None,
            call_id=context.call_id if context is not None else None,
            request_id=context.request_id if context is not None else None,
        ) from None


class A2AFacade:
    """``donkey.a2a``: send official SDK requests through a governed gateway.

    Provisional (ADR 0020): may change in a minor release with a migration
    note. ADR 0022 makes this facade an exception to the stable-root rule.
    No OAuth completion, retries, resubscription or remote cancellation is performed.
    Docs: https://docs.donkey-kit.dev/tool-access/a2a
    """

    def __init__(
        self,
        registry: ExchangeRegistry,
        cfg: DonkeyConfig,
        shared: DonkeyAsyncClient,
        llm_auth: AuthProvider | None,
    ) -> None:
        self._registry = registry
        self._cfg = cfg
        self._shared = shared
        self._auth = gateway_auth(cfg, llm_auth)

    @asynccontextmanager
    async def _transport(
        self, ref: AssetRef | str, auth: McpAuth | None, criteria: GovernanceCriteria | None
    ) -> AsyncIterator[tuple[ClientTransport, list[httpx.Response]]]:
        try:
            from a2a.client.card_resolver import parse_agent_card
            from a2a.client.transports.jsonrpc import JsonRpcTransport
            from a2a.compat.v0_3.jsonrpc_transport import CompatJsonRpcTransport
            from a2a.utils.constants import (
                AGENT_CARD_WELL_KNOWN_PATH,
                PROTOCOL_VERSION_0_3,
                PROTOCOL_VERSION_1_0,
                VERSION_HEADER,
                TransportProtocol,
            )
        except ImportError:
            raise ImportError(
                'donkey.a2a needs the a2a extra. Install it with: pip install "donkey-kit[a2a]"'
            ) from None

        if not self._cfg.allow_a2a_sdk_telemetry:
            _check_sdk_telemetry()

        if not isinstance(ref, (AssetRef, str)):
            raise RegistryError(
                "A2A calls require an Exchange asset reference, not a handle or URL."
            )
        asset = AssetRef.parse(ref)
        report = await self._registry.explain(asset, criteria=criteria or GovernanceCriteria())
        if not report.governed:
            raise RegistryError(
                "Refusing to call an A2A agent that did not pass governance checks. "
                "No gateway request was sent.",
                remediation=(
                    "Run donkey.registry.explain(ref, criteria=...) to inspect the verdict."
                ),
            )
        handle = await self._registry.resolve_agent(asset)
        endpoint = handle.endpoint_url
        credential = auth if auth is not None else self._auth

        with outbound_call(
            telemetry=self._cfg.telemetry,
            cost=self._cfg.cost,
            capture_content=self._cfg.telemetry_capture_content,
        ) as run:

            async def headers(_request: httpx.Request) -> Mapping[str, str]:
                user = current_user()
                check_gateway_endpoint(
                    self._cfg,
                    credential=("code" if auth is not None else "config")
                    if credential is not None
                    else None,
                    user=user is not None,
                )
                result = dict(await credential.headers()) if credential is not None else {}
                if user is not None:
                    name = user_token_header(self._cfg)
                    if any(key.lower() == name.lower() for key in result):
                        raise ConfigError(
                            "A2A service and user credentials use the same header. "
                            "Set user_token_header to a distinct gateway user-token header."
                        )
                    result[name] = (
                        f"{_verify.LLM_PROXY_WALLET_JWT_SCHEME} {user.token}"
                        if name.lower() == _verify.LLM_PROXY_WALLET_JWT_HEADER.lower()
                        else user.token
                    )
                result.update(run.trace_headers)
                result[self._cfg.correlation_header or _verify.CORRELATION_ID_HEADER] = (
                    run.correlation_id
                )
                return result

            view = self._shared.a2a_view(endpoint, headers=headers)
            # The official SDK has nested streaming generators whose outer aclose()
            # does not immediately close the inner response. Track responses through
            # the public httpx hook so early exit closes sockets in this same task.
            responses: list[httpx.Response] = []

            async def remember(response: httpx.Response) -> None:
                responses.append(response)
                if response.request.method == "POST" and response.is_success:
                    run.record_quality(response.headers)

            view.event_hooks["response"].append(remember)
            transport: ClientTransport | None = None
            try:
                with _protocol_errors(responses):
                    response = await view.get(
                        endpoint.rstrip("/") + "/" + AGENT_CARD_WELL_KNOWN_PATH.lstrip("/")
                    )
                    card = parse_agent_card(response.json())
                    # Only JSON-RPC can use this exact pinned endpoint. REST adds
                    # paths and gRPC bypasses the governed httpx transport.
                    interface = next(
                        (
                            i
                            for i in card.supported_interfaces
                            if i.protocol_binding == TransportProtocol.JSONRPC
                            and i.protocol_version
                            in {
                                PROTOCOL_VERSION_0_3,
                                PROTOCOL_VERSION_0_3 + ".0",
                                PROTOCOL_VERSION_1_0,
                                PROTOCOL_VERSION_1_0 + ".0",
                            }
                        ),
                        None,
                    )
                    if interface is None:
                        raise ToolInvocationError(
                            "The A2A agent does not advertise a supported JSON-RPC transport.",
                            remediation="Expose a JSON-RPC A2A interface through the gateway.",
                        )
                    version = interface.protocol_version

                    async def negotiated_version(request: httpx.Request) -> None:
                        if request.method == "POST":
                            request.headers[VERSION_HEADER] = version

                    view.event_hooks["request"].append(negotiated_version)
                    cls = (
                        CompatJsonRpcTransport
                        if interface.protocol_version
                        in {PROTOCOL_VERSION_0_3, PROTOCOL_VERSION_0_3 + ".0"}
                        else JsonRpcTransport
                    )
                    transport = cls(view, card, endpoint)
                yield transport, responses
            finally:
                closers = [response.aclose for response in responses]
                if transport is not None:
                    closers.append(transport.close)
                closers.append(view.aclose)
                await _cleanup(closers)

    async def send(
        self,
        ref: AssetRef | str,
        message: SendMessageRequest,
        *,
        auth: McpAuth | None = None,
        criteria: GovernanceCriteria | None = None,
    ) -> SendMessageResponse:
        """Send an official SDK request after governance and endpoint checks.

        Args:
            ref: Exchange ``group_id/asset_id/version``, never a handle's URL.
            message: The official ``a2a.types.SendMessageRequest``.
            auth: Gateway credential override; otherwise use configured consumer auth.
            criteria: Governance requirements; defaults to ``GovernanceCriteria()``.

        Raises:
            ImportError: Install ``donkey-kit[a2a]`` to use this surface.
            RegistryError: The asset fails governance or endpoint resolution.
            ConfigError: SDK tracing lacks permission, or credential provenance
                or user-token headers are unsafe.
            DonkeyError: The gateway refuses the call.
            GatewayAuthRequired: A captured v0.3 task challenge needs caller-owned OAuth.
            ToolInvocationError: SDK protocol parsing or invocation fails.

        Docs: https://docs.donkey-kit.dev/tool-access/a2a
        """
        async with self._transport(ref, auth, criteria) as (transport, responses):
            with _protocol_errors(responses):
                response = await transport.send_message(message)
                if response.WhichOneof("payload") is None:
                    raise ValueError("Missing A2A response payload")
                _auth_challenge(response, transport, responses)
                return response

    @asynccontextmanager
    async def stream(
        self,
        ref: AssetRef | str,
        message: SendMessageRequest,
        *,
        auth: McpAuth | None = None,
        criteria: GovernanceCriteria | None = None,
    ) -> AsyncIterator[AsyncIterator[StreamResponse]]:
        """Yield official SDK streaming events within a resource-owning context.

        Use ``async with donkey.a2a.stream(ref, request) as events``, then
        ``async for event in events``. Leaving the context closes the local
        response and span, even after a break; it never cancels the remote task.
        Arguments and failures are the same as :meth:`send`.

        Docs: https://docs.donkey-kit.dev/tool-access/a2a
        """
        async with self._transport(ref, auth, criteria) as (transport, responses):
            stream = transport.send_message_streaming(message)

            async def events() -> AsyncGenerator[StreamResponse, None]:
                received = False
                while True:
                    with _protocol_errors(responses):
                        try:
                            event = await anext(stream)
                        except StopAsyncIteration:
                            if not received:
                                raise ValueError("A2A stream ended without a response") from None
                            return
                        if event.WhichOneof("payload") is None:
                            raise ValueError("Missing A2A stream payload")
                        _auth_challenge(event, transport, responses)
                        received = True
                    yield event

            iterator = events()
            try:
                yield iterator
            finally:
                await _cleanup([iterator.aclose, stream.aclose])

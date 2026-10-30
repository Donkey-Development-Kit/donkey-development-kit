"""The A2A server behind ``donkey.serve()`` (`BG §2.9.1`, #224).

Every A2A behaviour here is the official ``a2a-sdk``'s: ``DefaultRequestHandler``
runs the task lifecycle over an ``InMemoryTaskStore`` (subclassed only to drop
the oldest finished tasks, so a long-running server's memory stays bounded),
``TaskUpdater`` publishes
the status and artifact events, and ``create_agent_card_routes`` /
``create_jsonrpc_routes`` serve the card and the JSON-RPC endpoint. Never
reimplement any of it here (the build plan's *Do not build* list). The class
names were read from the installed ``a2a-sdk`` (``docs/verified-apis.md`` §8.5).

The ``a2a-sdk`` is imported inside :func:`build_app`, so importing this module
needs no ``[a2a]`` extra; only building the server does.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Protocol

from ..core.endpoints import is_loopback_host
from ..core.errors import ConfigWarning, DonkeyError, PolicyViolation
from ..core.inbound import InboundRun, inbound_task
from ..core.refusals import TypedRefusals
from ..core.toolspec import AgentSpec, agent_spec
from .types import A2AResult, A2ATask

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from a2a.server.context import ServerCallContext
    from a2a.server.events import EventQueue
    from a2a.types import Message, Task

__all__ = ["build_app", "serve"]

_log = logging.getLogger(__name__)

# The media types a served agent accepts and produces: text parts and data parts,
# the two A2ATask and A2AResult carry.
_MODES = ("text/plain", "application/json")

# How many finished tasks (completed, failed, canceled or rejected) the server
# keeps for GetTask; past it, the oldest finished task is dropped. A task still
# running is never dropped.
_MAX_FINISHED_TASKS = 1000


class _ASGIApp(Protocol):
    """The ASGI callable :func:`build_app` returns, so callers
    (``httpx.ASGITransport``, ``uvicorn.run``, your own server) and ``mypy``
    never see a ``starlette`` or ``a2a-sdk`` type."""

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        """Handle one ASGI connection."""
        ...


def _missing_extra(exc: ImportError) -> ImportError:
    detail = f" (no module named {exc.name!r})" if exc.name else ""
    return ImportError(
        f"donkey.serve() needs the 'a2a' extra{detail}. Install it with:\n"
        '    pip install "donkey-kit[a2a]"'
    )


def _spec_of(handler: Callable[..., Any]) -> AgentSpec:
    spec = agent_spec(handler)
    if spec is None:
        name = getattr(handler, "__qualname__", repr(handler))
        raise TypeError(
            f"{name} is not marked with @donkey.agent. The agent card is generated "
            'from that marker, so add it: @donkey.agent(name="my-agent", skills=[...]).'
        )
    return spec


def _card_fields(spec: AgentSpec, *, url: str, version: str) -> dict[str, Any]:
    """The agent card, from the ``@donkey.agent`` record and nothing else."""
    return {
        "name": spec.name,
        "description": spec.docstring,
        "version": version,
        "supported_interfaces": [
            {"url": url, "protocol_binding": "JSONRPC", "protocol_version": "1.0"}
        ],
        "capabilities": {"streaming": False},
        "default_input_modes": list(_MODES),
        "default_output_modes": list(_MODES),
        "skills": [
            {"id": skill, "name": skill, "description": skill, "tags": [skill]}
            for skill in spec.skills
        ],
    }


async def _run(spec: AgentSpec, task: A2ATask) -> A2AResult:
    if spec.is_async:
        result = await spec.func(task)
    else:
        # to_thread copies the context, so the bound run and span reach the thread.
        result = await asyncio.to_thread(spec.func, task)
    if isinstance(result, str):
        return A2AResult(text=result)
    if not isinstance(result, A2AResult):
        raise TypeError(
            f"an A2A handler returns a str or an A2AResult, not {type(result).__name__}"
        )
    return result


def _headers(context: RequestContext) -> dict[str, str]:
    """The inbound HTTP request's headers: the ``a2a-sdk``'s default call-context
    builder copies them into ``call_context.state["headers"]``."""
    headers = context.call_context.state.get("headers")
    return dict(headers) if isinstance(headers, dict) else {}


def _failure(exc: Exception, run: InboundRun | None) -> tuple[str, dict[str, Any]]:
    """The text and the structured ``error`` part a failed task carries.

    The exception's type name and the ids only: its message and a refusal's
    remediation are for the agent's operator and go to the log, since either may
    repeat request content or name internal configuration.
    """
    name = type(exc).__name__
    refused = isinstance(exc, PolicyViolation)
    text = (
        f"the agent's downstream call was refused: {name}"
        if refused
        else f"the agent failed: {name}"
    )
    error: dict[str, Any] = {"type": name, "refusal": refused}
    if run is not None:
        error["correlation_id"] = run.correlation_id
    if isinstance(exc, DonkeyError) and exc.call_id is not None:
        error["call_id"] = exc.call_id
    return text, {"error": error}


def build_app(
    handler: Callable[..., Any],
    *,
    url: str = "http://127.0.0.1:8000/",
    version: str = "0.0.0",
    telemetry: bool = True,
    correlation_header: str | None = None,
    refusals: TypedRefusals | None = None,
) -> _ASGIApp:
    """Build the A2A server for a ``@donkey.agent`` handler as an ASGI app.

    ``Donkey.serve()`` runs this app on a port; call it directly to mount the
    agent in your own ASGI server or to test it in process with
    ``httpx.ASGITransport``.

    The handler takes an :class:`~donkey_kit.a2a.A2ATask` and returns a ``str``
    or an :class:`~donkey_kit.a2a.A2AResult`; a sync handler runs in a worker
    thread.

    Each task is governed (#225): it runs in a ``donkey.run()`` scope whose
    correlation ID is the caller's ``X-Correlation-Id`` when it sent one and a
    fresh ID when not (:attr:`A2ATask.correlation_id`), so every downstream call
    sends it along with the configured cost tags. A ``donkey.a2a.task`` span wraps
    the handler, parenting its calls' spans. If the handler raises, the task fails
    with a text part naming the exception type and an ``error`` data part
    (``type``, ``refusal``, ``correlation_id``, and the failed call's ``call_id``
    for a ``DonkeyError``); a governance refusal is re-raised as its typed
    ``DonkeyError`` first, so ``type`` is ``PIIDetected``, not the framework's
    wrapper. The message and stack trace go to this module's logger only.

    Tasks live in memory, in the ``a2a-sdk``'s ``InMemoryTaskStore``. The 1000
    most recently finished ones (completed, failed, canceled or rejected) stay
    readable with ``GetTask``; an older one is dropped, so a long-running server
    does not grow without bound. A running task is never dropped.

    The app authenticates no caller: the gateway in front of it does.

    Args:
        handler: A callable marked with ``@donkey.agent``.
        url: The URL the card advertises for the JSON-RPC endpoint. When a
            gateway fronts the agent, pass the gateway's URL.
        version: The agent version the card advertises.
        telemetry: Open the per-task span; ``Donkey.serve()`` passes the
            ``telemetry`` setting.
        correlation_header: The inbound correlation header's name;
            ``Donkey.serve()`` passes the ``correlation_header`` setting.
            ``None`` reads ``X-Correlation-Id``.
        refusals: The typed-refusal bridge applied to a handler's exception.
            ``Donkey.serve()`` passes ``donkey_kit.typed_refusals()``, which knows
            every imported framework's wrapper; ``None`` applies the core bridge.

    Returns:
        A Starlette application serving the agent card at
        ``/.well-known/agent-card.json`` and JSON-RPC at ``/``. Run it inside
        its ASGI lifespan, as ``uvicorn`` does, so shutdown drains in-flight
        tasks.

    Raises:
        TypeError: ``handler`` is not marked with ``@donkey.agent``.
        ImportError: The ``[a2a]`` extra is not installed.

    Docs: https://docs.donkey-kit.dev/a2a
    """
    spec = _spec_of(handler)
    try:
        from a2a.helpers import (
            get_data_parts,
            get_text_parts,
            new_data_part,
            new_message,
            new_task_from_user_message,
            new_text_part,
        )
        from a2a.server.agent_execution import AgentExecutor
        from a2a.server.request_handlers import DefaultRequestHandler
        from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
        from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
        from a2a.types import AgentCard, TaskState
        from starlette.applications import Starlette
    except ImportError as exc:
        raise _missing_extra(exc) from exc

    bridge = refusals if refusals is not None else TypedRefusals()
    finished_states = {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_REJECTED,
    }

    class _BoundedTaskStore(InMemoryTaskStore):
        """The a2a-sdk's store, keeping at most ``_MAX_FINISHED_TASKS`` finished
        tasks: the oldest is deleted through the store's own ``delete``."""

        def __init__(self) -> None:
            super().__init__()
            self._finished: OrderedDict[str, ServerCallContext] = OrderedDict()

        async def save(self, task: Task, context: ServerCallContext) -> None:
            await super().save(task, context)
            if task.status.state not in finished_states:
                self._finished.pop(task.id, None)
                return
            self._finished[task.id] = context
            self._finished.move_to_end(task.id)
            while len(self._finished) > _MAX_FINISHED_TASKS:
                oldest, owner = self._finished.popitem(last=False)
                await super().delete(oldest, owner)

        async def delete(self, task_id: str, context: ServerCallContext) -> None:
            self._finished.pop(task_id, None)
            await super().delete(task_id, context)

    def _failed_message(exc: Exception, run: InboundRun | None, context: RequestContext) -> Message:
        text, error = _failure(exc, run)
        return new_message(
            [new_text_part(text), new_data_part(error)],
            context_id=context.context_id,
            task_id=context.task_id,
        )

    class _Executor(AgentExecutor):
        async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
            message = context.message
            if context.current_task is None:
                await event_queue.enqueue_event(new_task_from_user_message(message))
            updater = TaskUpdater(event_queue, context.task_id, context.context_id)
            await updater.start_work()
            parts = list(message.parts) if message is not None else []
            run: InboundRun | None = None
            try:
                with inbound_task(
                    _headers(context),
                    telemetry=telemetry,
                    refusals=bridge,
                    header=correlation_header,
                    attributes={
                        "donkey.a2a.agent": spec.name,
                        "donkey.a2a.task_id": context.task_id,
                        "donkey.a2a.context_id": context.context_id,
                    },
                ) as run:
                    task = A2ATask(
                        input="\n".join(get_text_parts(parts)),
                        data=tuple(get_data_parts(parts)),
                        task_id=context.task_id,
                        context_id=context.context_id,
                        message=message,
                        correlation_id=run.correlation_id,
                    )
                    result = await _run(spec, task)
            except Exception as exc:  # noqa: BLE001 — any handler failure fails the task, never the server
                _log.exception(
                    "A2A agent %r failed task %s (correlation id %s)",
                    spec.name,
                    context.task_id,
                    run.correlation_id if run is not None else None,
                )
                await updater.failed(_failed_message(exc, run, context))
                return
            if result.text is not None:
                await updater.add_artifact([new_text_part(result.text)])
            if result.data is not None:
                await updater.add_artifact([new_data_part(result.data)])
            await updater.complete()

        async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
            updater = TaskUpdater(event_queue, context.task_id, context.context_id)
            await updater.cancel()

    card = AgentCard(**_card_fields(spec, url=url, version=version))
    handler_ = DefaultRequestHandler(
        agent_executor=_Executor(), task_store=_BoundedTaskStore(), agent_card=card
    )
    routes = [*create_agent_card_routes(card), *create_jsonrpc_routes(handler_, "/")]

    @asynccontextmanager
    async def lifespan(_app: object) -> AsyncIterator[None]:
        # The a2a-sdk keeps per-task asyncio tasks alive past the response;
        # draining them on shutdown leaves none pending when the loop closes.
        yield
        await handler_.aclose()

    app: _ASGIApp = Starlette(routes=routes, lifespan=lifespan)
    return app


def serve(
    handler: Callable[..., Any],
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    url: str | None = None,
    version: str = "0.0.0",
    telemetry: bool = True,
    correlation_header: str | None = None,
    refusals: TypedRefusals | None = None,
) -> None:
    """Serve a ``@donkey.agent`` handler over A2A on ``host:port`` via uvicorn,
    blocking until the server stops. ``Donkey.serve()`` documents the first four
    arguments and :func:`build_app` the governance ones; ``Donkey.serve()`` fills
    those from its configuration.

    A ``host`` that is not loopback (``localhost``, ``127.0.0.0/8``, ``::1``)
    emits a :class:`~donkey_kit.core.errors.ConfigWarning`: the server
    authenticates no caller, so only the gateway should be able to reach it.

    Raises:
        TypeError: ``handler`` is not marked with ``@donkey.agent``.
        ImportError: The ``[a2a]`` extra is not installed.

    Docs: https://docs.donkey-kit.dev/a2a
    """
    app = build_app(
        handler,
        url=url or f"http://{host}:{port}/",
        version=version,
        telemetry=telemetry,
        correlation_header=correlation_header,
        refusals=refusals,
    )
    try:
        import uvicorn
    except ImportError as exc:
        raise _missing_extra(exc) from exc
    if not is_loopback_host(host):
        warnings.warn(
            f"donkey.serve() is binding {host}, which other machines can reach. The A2A "
            "server authenticates no caller: put the gateway in front of it (donkey "
            "expose) and let only the gateway reach this port.",
            ConfigWarning,
            stacklevel=2,
        )
    uvicorn.run(app, host=host, port=port)

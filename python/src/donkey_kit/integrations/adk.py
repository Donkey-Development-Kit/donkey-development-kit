"""Google ADK adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Two factories, one per proxy ingress Format (docs/verified-apis.md §2):

* ``model()`` — ADK's ``LiteLlm`` wrapper for a ``Format=OpenAI`` proxy (the
  SDK's default DDK proxies). LiteLLM takes ``openai/<id>`` model strings and
  speaks ``/chat/completions``. Header injection is via LiteLLM's
  ``extra_headers``. LiteLLM takes a pre-built OpenAI client (``client``), so
  we pass an ``AsyncOpenAI`` that sends through the shared client: redirects
  are not followed, credentials go only to checked endpoints, and each call
  carries the run's correlation id and populates ``donkey.last_call`` (#740).
  ADK requires ``litellm>=1.84`` (floor, not ceiling). LiteLLM sets the
  client's ``max_retries`` on every call (default 2), so the kwarg goes to
  LiteLLM itself (#734). LiteLLM re-raises a gateway refusal around a response
  it rebuilt, so ``model()`` also passes ADK's ``llm_client`` hook: a
  ``LiteLLMClient`` subclass that tags the error with the typed refusal the
  transport recorded for that call, which the typed-refusal bridge then raises
  (#969). A ``LiteLlm`` built from ``connection_kwargs()`` does not get it.
* ``gemini()`` — ADK's native ``google.adk.models.Gemini`` for a
  ``Format=Gemini`` proxy (#691). The native route is
  ``POST <proxy>/models/<model>:generateContent`` (#540); the model travels in
  the URL only, and the ingress ignores a body ``model`` (docs/verified-apis.md §2).
  ``google-genai`` accepts ``HttpOptions.httpx_async_client``, so we hand it the
  shared :class:`~donkey_kit.core.transport.DonkeyAsyncClient`: full injection —
  per-run correlation, SDK retries, rotating JWTs and ``donkey.last_call`` all
  work. The default DDK proxies are
  ``Format=OpenAI``, so point ``base_url`` at a ``Format=Gemini`` proxy.

Both factories return a subclass of the ADK class that carries
``donkey.last_call`` back to the context that started the model call (#1207).
google-adk 2.13.0 runs each step of the model's response generator in a copy of
the flow's context, so without it the record stays in that copy and an
``after_model_callback`` reads ``UNOBSERVED``. :func:`last_call_bridged` returns
that subclass for a model built from ``connection_kwargs()`` or
``gemini_connection_kwargs()`` (#1218).

APPROVALS (BG §2.3, #1235): an approval-gated tool
(``@donkey.tool(approval="required")`` in an agent's ``tools``) pauses the run
with ADK's own tool confirmation once the app has the plugin
:func:`approval_plugin` builds: ``App(..., plugins=[approval_plugin()])``.
Before the call runs, the plugin records the pending approval and calls
``tool_context.request_confirmation``, and ADK ends the turn with an
``adk_request_confirmation`` call. :func:`pending_approvals` reads those calls
off the run's events, and the run resumes on a user message whose parts are
:func:`approve` / :func:`reject` responses. An approved call runs once, under
the pause's correlation id. A rejected call is not run: the model gets the
rejection as the tool's error result, and nothing raises ``ApprovalRejected``.
The pause lives in the session's events, so a fresh process can resume it.
Without the plugin the gate raises ``ApprovalRequired`` inside the tool.

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

import collections
import contextvars
import functools
import hashlib
import json
import math
import threading
import time
from collections.abc import Iterable, Mapping
from contextlib import aclosing
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeVar

from ..core import _verify
from ..core.approvals import Approval, record_decision, record_pending
from ..core.correlation import current_correlation_id
from ..core.lastcall import LastCallBridge, open_last_call_bridge
from ..core.masking import masked
from ..core.refusals import capture_refusals
from ..core.toolspec import tool_spec
from . import AdapterCapabilities
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from google.adk.models import BaseLlm, Gemini
    from google.adk.models.lite_llm import LiteLlm, LiteLLMClient
    from google.adk.plugins import BasePlugin
    from google.genai import types

_M = TypeVar("_M", bound="BaseLlm")

__all__ = [
    "APPROVAL_KIND",
    "ADKAdapter",
    "approval_plugin",
    "approve",
    "approved_call",
    "gemini",
    "last_call_bridged",
    "model",
    "pending_approvals",
    "reject",
]


class ADKAdapter(Adapter):
    """Governed Google ADK objects, reached as ``donkey.adk``.

    Each factory returns the framework's own native object, pointed at the governed
    LLM proxy with the SDK's headers and transport: ``model(model)`` builds a
    ``LiteLlm`` and ``gemini(model)`` a ``Gemini``. ``connection_kwargs()`` returns
    the same settings for building it yourself.

    Supported at ``connection_kwargs()`` only (`BG §1.8`): that accessor is the
    supported surface, and the factories are conveniences over it.

    Raises:
        ImportError: ``donkey.adk`` was read without the ``adk`` extra installed;
            the message carries the install command.
        ConfigError: The LLM-proxy settings are missing or incomplete.

    Docs: https://docs.donkey-kit.dev/frameworks/adk
    """

    # Both send through the shared client and observe donkey.last_call (#691,
    # #946). model()'s typed refusals come from its llm_client, which tags
    # LiteLLM's error with the refusal the transport recorded for the call
    # (#969). Each factory has its own capabilities (#741, #726).
    factories = MappingProxyType(
        {
            "model": AdapterCapabilities(
                transport="shared",
                sync=False,
                streaming=True,
                typed_refusals=True,
                observes_last_call=True,
            ),
            "gemini": AdapterCapabilities(
                transport="shared",
                sync=False,
                streaming=True,
                typed_refusals=True,
                observes_last_call=True,
            ),
        }
    )

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for a ``LiteLlm(model="openai/<id>", **kwargs)`` you
        build yourself. LiteLLM uses ``api_base``/``extra_headers`` (not
        ``base_url``/``default_headers``). ``client`` is an ``AsyncOpenAI`` that
        sends through the SDK's shared client, which does not follow redirects
        and sends credentials only to checked endpoints; LiteLLM's OpenAI route
        uses it in place of the client it would build."""
        conn = self._connection()
        return masked(
            {
                "api_base": conn["base_url"],
                "api_key": conn["api_key"],
                "extra_headers": conn["default_headers"],
                # LiteLLM sets this on ``client`` per call, default 2.
                "max_retries": 0,  # we retry in transport (BG §1.1)
                **self._proxy_openai_client_kwarg("client"),
            }
        )

    def gemini_connection_kwargs(self, *, base_url: str | None = None) -> dict[str, Any]:
        """Governed kwargs for a ``Gemini(model=<id>, **kwargs)`` you build
        yourself, bound to a ``Format=Gemini`` proxy (``base_url``, default the
        configured proxy URL).

        ADK builds its client from ``client_kwargs`` (from google-adk 2.13.0 it
        adds its tracking headers and fills only the fields left unset), so every
        governed value rides there: the shared http client (header injection,
        retries, last_call), the consumer-auth headers, the ``api_key`` slot
        google-genai requires for the Gemini API backend, and the SDK timeout —
        google-genai otherwise sends ``timeout=None``, which disables the
        client's. ``api_version=""`` because the proxy route has no
        ``/v1beta`` segment. A ``base_url`` passed here must pass the https check."""
        self._allow_endpoints({"base_url": base_url}, "base_url")
        conn = self._connection("gemini")
        url = base_url or conn["base_url"]
        return masked(
            {
                "base_url": url,
                "client_kwargs": {
                    "api_key": conn["api_key"],
                    "http_options": {
                        "base_url": url,
                        "api_version": "",
                        "headers": conn["default_headers"],
                        "timeout": int(self._cfg.timeout_s * 1000),
                        "httpx_async_client": self.http_client(),
                    },
                },
            }
        )

    def model(self, model: str, **kw: Any) -> LiteLlm:
        """Return ADK's ``LiteLlm`` at the proxy. An ``api_base``/``base_url``
        override must pass the https check.

        Its ``llm_client`` is a ``LiteLLMClient`` subclass that lets a gateway
        refusal reach ``donkey.run()`` as its typed class (#969, see the module
        docstring). An ``llm_client`` you pass replaces it, and the refusal
        then stays LiteLLM's error.

        The object is a ``LiteLlm`` subclass that carries ``donkey.last_call``
        back to the context that started each model call, so an
        ``after_model_callback`` reads it (#1207)."""
        self._allow_endpoints(kw, "api_base", "base_url")
        with self._native_import():
            from google.adk.models.lite_llm import (  # VERIFY name/path: docs/verified-apis.md §8
                LiteLlm,
                LiteLLMClient,
            )

        conn = self.connection_kwargs()
        override = kw.get("api_base") or kw.get("base_url")
        if override is not None and "client" in conn:
            # LiteLLM sends to the client's own base URL, so rebuild it there.
            conn["client"] = self._proxy_openai_client(str(override))
        conn["llm_client"] = _governed_llm_client_class(LiteLLMClient)()
        governed: type[LiteLlm] = _last_call_bridged_class(LiteLlm)
        # LiteLLM's OpenAI-compatible route needs the ``openai/`` prefix.
        return governed(model=f"openai/{model}", **{**conn, **kw})

    def gemini(self, model: str, *, base_url: str | None = None, **kw: Any) -> Gemini:
        """Return ADK's native ``google.adk.models.Gemini`` bound to a
        ``Format=Gemini`` proxy, with the shared http client injected (#691).
        Pass the bare model id (``"gemini-3.8-flash"``) — no provider prefix.

        The object is a ``Gemini`` subclass that carries ``donkey.last_call``
        back to the context that started each model call, so an
        ``after_model_callback`` reads it (#1207, see the module docstring)."""
        conn = self.gemini_connection_kwargs(base_url=base_url)
        with self._native_import():
            from google.adk.models import Gemini  # VERIFY name/path: docs/verified-apis.md §8

        # ADK's pydantic config ignores unknown fields, so on google-adk < 2.4 (no
        # ``client_kwargs``; no ``base_url`` before 2.0) both kwargs are dropped
        # silently and the model talks to Google directly, bypassing the gateway
        # (#735). Refuse rather than return an ungoverned model.
        fields = getattr(Gemini, "model_fields", {})
        missing = sorted({"base_url", "client_kwargs"} - set(fields))
        if missing:
            raise _verify.blocked(
                "google.adk.models.Gemini lacks "
                + ", ".join(missing)
                + " (docs/verified-apis.md §8). donkey.adk.gemini() needs "
                "google-adk>=2.4, the first release with Gemini.client_kwargs; on "
                "older versions ADK drops the governed client without an error. "
                "Upgrade with: pip install -U 'donkey-kit[adk]'"
            )
        governed: type[Gemini] = _last_call_bridged_class(Gemini)
        return governed(model=model, **{**conn, **kw})


def last_call_bridged(model_class: type[_M]) -> type[_M]:
    """The subclass of an ADK model class that carries ``donkey.last_call`` back
    to the context that started each model call, so an ``after_model_callback``
    reads it (#1218). For a model you build yourself from
    :meth:`ADKAdapter.connection_kwargs` or
    :meth:`ADKAdapter.gemini_connection_kwargs`::

        llm = last_call_bridged(LiteLlm)(model="openai/gpt-4o", **donkey.adk.connection_kwargs())

    ``model_class`` is any ``google.adk.models.BaseLlm`` subclass, your own
    included. The subclass is built once per class, and a class that already
    bridges is returned as is. :meth:`ADKAdapter.model` and
    :meth:`ADKAdapter.gemini` already return one.

    Raises:
        TypeError: ``model_class`` is not a ``BaseLlm`` subclass.
        ImportError: ``google-adk`` is not installed.
    """
    from google.adk.models import BaseLlm

    if not (isinstance(model_class, type) and issubclass(model_class, BaseLlm)):
        raise TypeError(f"expected a google.adk.models.BaseLlm subclass, got {model_class!r}")
    if getattr(model_class, "_donkey_last_call_bridged", False):
        return model_class
    # A class hashes by identity; mypy reads pydantic's unhashable instances.
    base: Any = model_class
    bridged: type[_M] = _last_call_bridged_class(base)
    return bridged


@functools.cache
def _last_call_bridged_class(base: Any) -> Any:
    """The subclass of an ADK model class (``base``: ``Gemini``, ``LiteLlm`` or
    another ``BaseLlm``) that :meth:`ADKAdapter.gemini`, :meth:`ADKAdapter.model`
    and :func:`last_call_bridged` build, defined on first use so ``google.adk``
    is imported lazily (§1.1).

    Only the public ``BaseLlm.generate_content_async`` is overridden. ADK's flow
    calls it in its own context and then iterates the generator it returns.
    From google-adk 2.13.0 it iterates that generator in a copy of the context
    (``_stream_in_caller_scope``), so the record the transport sets stays in the
    copy, and the ``after_model_callback`` that ADK runs in the flow's context
    reads ``UNOBSERVED`` (docs/verified-apis.md §8). The override is a plain
    ``def``, so it opens a :class:`~donkey_kit.core.lastcall.LastCallBridge`
    in the flow's context before ADK takes the copy. The copy inherits the
    bridge, and the transport records through it.

    The bridge serves one model call in one task, so hazard #2 holds: a
    ``ParallelAgent`` branch runs in a task of its own and opens its own bridge.
    It closes before the generator yields a complete (non-partial) response,
    because ADK runs that response's function calls before it resumes the
    generator, and a tool that ADK runs in a task of its own must not write
    through the bridge. A partial response keeps it open, since ADK runs no
    function calls for one."""

    class LastCallBridged(base):
        """An ADK model whose ``donkey.last_call`` reaches the context that
        started the call (#1207)."""

        _donkey_last_call_bridged = True

        def generate_content_async(self, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
            bridge = open_last_call_bridge()
            return _bridged_responses(super().generate_content_async(*args, **kwargs), bridge)

    LastCallBridged.__name__ = LastCallBridged.__qualname__ = f"Governed{base.__name__}"
    return LastCallBridged


async def _bridged_responses(
    responses: AsyncGenerator[Any, None], bridge: LastCallBridge
) -> AsyncGenerator[Any, None]:
    """Yield ``responses`` and close ``bridge`` before the first complete
    response, or when the call ends early (see :func:`_last_call_bridged_class`)."""
    try:
        async with aclosing(responses):
            async for response in responses:
                if not getattr(response, "partial", False):
                    bridge.close()
                yield response
    finally:
        bridge.close()


@functools.cache
def _governed_llm_client_class(base: Any) -> type[LiteLLMClient]:
    """The subclass of ADK's ``LiteLLMClient`` (``base``) that
    :meth:`ADKAdapter.model` passes as ``llm_client``, defined on first use so
    ``google.adk`` is imported lazily (§1.1).

    ``LiteLlm`` awaits ``llm_client.acompletion`` for every model request,
    streamed or not (docs/verified-apis.md §8). LiteLLM raises a gateway refusal
    as its own error around a response it rebuilt (no gateway headers, no
    cause), so the override opens a refusal capture around the call and tags
    the error it raised with the typed error the transport recorded. The error
    itself is re-raised unchanged; ``donkey.run()`` and ``typed_refusals()``
    raise the tagged refusal in its place."""

    class GovernedLiteLLMClient(base):
        """ADK's ``LiteLLMClient``, with LiteLLM's errors tagged with the typed
        refusal behind them (#969)."""

        async def acompletion(self, *args: Any, **kwargs: Any) -> Any:
            with capture_refusals() as capture:
                try:
                    return await super().acompletion(*args, **kwargs)
                except Exception as exc:
                    capture.tag(exc)
                    raise

    return GovernedLiteLLMClient


def model(model: str, **kw: Any) -> LiteLlm:
    """Module-level convenience: a native ``LiteLlm`` at the proxy using a cached
    default env-configured Donkey. Equivalent to
    ``Donkey.from_env().adk.model(model, **kw)``."""
    return default_adapter(ADKAdapter).model(model, **kw)


def gemini(model: str, *, base_url: str | None = None, **kw: Any) -> Gemini:
    """Module-level convenience: a native ``Gemini`` at a ``Format=Gemini`` proxy
    using a cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().adk.gemini(model, base_url=..., **kw)``."""
    return default_adapter(ADKAdapter).gemini(model, base_url=base_url, **kw)


#: The ``kind`` in a DDK approval's confirmation payload, so a caller reading the
#: run's ``adk_request_confirmation`` calls can tell it from other confirmations.
APPROVAL_KIND = "donkey.approval"

# The function call ADK pauses a run with (docs/verified-apis.md §8).
_CONFIRMATION_CALL = "adk_request_confirmation"

# How many invocations' recorded decisions the plugin keeps.
_INVOCATIONS_KEPT = 256

# The call the plugin approved, for the gate inside the tool to pick up. Set in
# before_tool_callback, which ADK awaits on the task that runs that one call (a
# task per call at google-adk 2.4.0 and 2.13.0), so it cannot reach another
# call. ADK runs a sync tool in a thread pool on a copy of that task's context.
_APPROVED: contextvars.ContextVar[Approval | None] = contextvars.ContextVar(
    "donkey_adk_approved", default=None
)


def approval_plugin(name: str = "donkey_approval") -> BasePlugin:
    """An ADK plugin that pauses approval-gated tool calls with ADK's own tool
    confirmation (BG §2.3, #1235).

    Add it to the app: ``App(name=..., root_agent=agent,
    plugins=[approval_plugin()])``. Before a ``@donkey.tool(approval="required")``
    call runs, the plugin records the pending approval and requests a
    confirmation. Its payload carries the ``kind`` (:data:`APPROVAL_KIND`),
    ``approval_id``, ``tool``, ``arguments``, ``risk`` and ``correlation_id``.
    Resume with ``types.Content(role="user", parts=[approve(c, by="m.keller")
    for c in pending_approvals(session.events)])``. The plugin checks the
    answer against the request the agent stored in the session: a confirmation
    it did not request, or for a call already answered, is refused and the
    call not run. It records the decision once, and an approved call runs
    under the pause's correlation id. A rejected
    call is answered with the rejection message, so the model sees it as the
    tool's error result. ADK keeps confirmation calls out of the model's
    context, so the approver's name never reaches it.

    The approval id is derived from ADK's function-call id, the tool and its
    arguments as the model sent them, so it is the same in any process. ADK
    holds the pause, so ``approval_timeout`` does not apply.

    Raises:
        ImportError: ``google-adk`` is not installed.
    """
    return _approval_plugin_class()(name=name)


@functools.cache
def _approval_plugin_class() -> type[BasePlugin]:
    """The ``BasePlugin`` subclass :func:`approval_plugin` builds, defined on
    first use so ``google.adk`` is imported lazily (§1.1)."""
    from google.adk.plugins import BasePlugin

    class ApprovalPlugin(BasePlugin):
        """Pauses approval-gated tool calls with ADK's tool confirmation."""

        def __init__(self, name: str) -> None:
            super().__init__(name=name)
            self._lock = threading.Lock()
            # Per invocation, the latest few: the approvals whose decision is
            # recorded, so a call ADK runs again does not record it twice.
            self._decided: collections.OrderedDict[str, set[str]] = collections.OrderedDict()

        async def before_tool_callback(
            self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any
        ) -> dict[str, Any] | None:
            func = getattr(tool, "func", None)
            spec = tool_spec(func) if callable(func) else None
            if spec is None or spec.approval != "required":
                return None
            # Bind as ADK calls the tool: it drops arguments the function does
            # not take and passes its ToolContext parameter itself.
            context_param = getattr(tool, "_context_param_name", None) or "tool_context"
            params = spec.signature.parameters
            sent = {k: v for k, v in tool_args.items() if k in params and k != context_param}
            given = {**sent, context_param: tool_context} if context_param in params else sent
            try:
                bound = spec.signature.bind(**given)
            except TypeError:
                return None  # ADK answers a missing argument itself, without running it
            bound.apply_defaults()
            arguments = MappingProxyType(
                {k: v for k, v in bound.arguments.items() if k != context_param}
            )
            call_id = str(tool_context.function_call_id)
            approval = _approval(call_id, spec.name, sent, arguments, spec.risk)
            confirmation = tool_context.tool_confirmation
            if confirmation is None:
                record_pending(approval)
                tool_context.request_confirmation(
                    hint=f"Approve the call of {approval.tool!r}?",
                    payload=_payload(approval, sent),
                )
                # The pause is not a tool result for the model to summarize;
                # without this the flow calls the model again.
                tool_context.actions.skip_summarization = True
                return _awaiting(approval)
            # Trust only what the agent wrote to the session: google-adk 2.4.0
            # resumes on any confirmation a user message carries, and again on
            # one it already ran.
            events = getattr(getattr(tool_context, "session", None), "events", None) or ()
            decision = _decision(confirmation, approval)
            requested = _requested(call_id, approval, events)
            refused = _refusal(call_id, approval, decision, requested, events)
            # The refusal is set whenever either is None; mypy needs both checks.
            if refused is not None or decision is None or requested is None:
                return {"error": refused}
            approved, by, message = decision
            approval = _as_requested(approval, requested)
            with self._lock:
                invocation = str(tool_context.invocation_id)
                decided = self._decided.setdefault(invocation, set())
                self._decided.move_to_end(invocation)
                while len(self._decided) > _INVOCATIONS_KEPT:
                    self._decided.popitem(last=False)
                record = approval.id not in decided
                decided.add(approval.id)
            if record:
                record_decision(approval, approved=approved, by=by)
            if approved:
                _APPROVED.set(approval)
                return None
            # The rejecter's name stays out of the model's context, as on the
            # other frameworks (#213, #1234); the decision span event records it.
            return {
                "error": message
                or f"A human rejected this call of {approval.tool!r}, so it was not run."
            }

        async def after_tool_callback(self, **kwargs: Any) -> None:
            _APPROVED.set(None)

        async def on_tool_error_callback(self, **kwargs: Any) -> None:
            _APPROVED.set(None)

        async def after_run_callback(self, *, invocation_context: Any) -> None:
            with self._lock:
                self._decided.pop(str(invocation_context.invocation_id), None)

    return ApprovalPlugin


def _approval(
    call_id: str,
    tool: str,
    sent: Mapping[str, Any],
    arguments: Mapping[str, Any],
    risk: str | None,
) -> Approval:
    # Hash the arguments as the model sent them, not the bound ones: a default's
    # repr can hold a memory address, which would change the id between
    # processes (a restored session).
    encoded = json.dumps(dict(sent), sort_keys=True, default=repr)
    approval_id = hashlib.sha256(repr((call_id, tool, encoded)).encode()).hexdigest()[:32]
    return Approval(
        id=approval_id,
        tool=tool,
        arguments=arguments,
        risk=risk,
        correlation_id=current_correlation_id() or approval_id,
        requested_at=time.time(),
        expires_at=math.inf,
    )


def _as_requested(approval: Approval, payload: Mapping[str, Any]) -> Approval:
    """``approval`` with the correlation id and time its pause recorded (the
    agent's ``payload`` in the session, never the answer's), so the resumed
    call joins the pause's trace, in this process or another."""
    correlation_id = payload.get("correlation_id")
    requested_at = payload.get("requested_at")
    return Approval(
        id=approval.id,
        tool=approval.tool,
        arguments=approval.arguments,
        risk=approval.risk,
        correlation_id=(
            correlation_id
            if isinstance(correlation_id, str) and correlation_id
            else approval.correlation_id
        ),
        requested_at=(
            float(requested_at)
            if isinstance(requested_at, (int, float)) and not isinstance(requested_at, bool)
            else approval.requested_at
        ),
        expires_at=approval.expires_at,
    )


def _payload(approval: Approval, sent: Mapping[str, Any]) -> dict[str, Any]:
    # The arguments as sent: the session stores the payload as JSON, and a bound
    # default need not serialize.
    return {
        "kind": APPROVAL_KIND,
        "approval_id": approval.id,
        "tool": approval.tool,
        "arguments": json.loads(json.dumps(dict(sent), default=repr)),
        "risk": approval.risk,
        "correlation_id": approval.correlation_id,
        "requested_at": approval.requested_at,
    }


def approved_call(tool: str) -> Approval | None:
    """The approval :func:`approval_plugin`'s plugin granted for the ``tool``
    call running now, or ``None``. The gate's ADK hook (BG §2.3): it lets the
    approved call run instead of raising ``ApprovalRequired``. One approval runs
    one call."""
    approval = _APPROVED.get()
    if approval is None or approval.tool != tool:
        return None
    _APPROVED.set(None)
    return approval


def pending_approvals(events: Iterable[Any]) -> tuple[Any, ...]:
    """The DDK approval requests among ADK ``events`` that no later event
    answers, in order.

    Pass the events of the latest run, or a session's ``events``. The runner
    does not yield the user message that answers a request, so the events of
    several runs put together still list a request already answered.

    Each is ADK's own ``adk_request_confirmation`` ``FunctionCall``; its
    ``args["toolConfirmation"]["payload"]`` is the payload described in
    :func:`approval_plugin`. Pass it to :func:`approve` or :func:`reject`."""
    calls: dict[str, Any] = {}
    for event in events:
        for call in event.get_function_calls() or ():
            if call.name == _CONFIRMATION_CALL and _kind(call) == APPROVAL_KIND:
                calls[call.id] = call
        for response in event.get_function_responses() or ():
            calls.pop(response.id, None)
    return tuple(calls.values())


def approve(request: Any, *, by: str) -> types.Part:
    """The user-message part that approves ``request`` (from
    :func:`pending_approvals`), recording ``by`` as the approver once the run
    resumes on it.

    Raises:
        ValueError: ``by`` is empty, or ``request`` is not a DDK approval request.
    """
    return _response(request, {"by": _who(by)}, confirmed=True)


def reject(request: Any, *, by: str, message: str | None = None) -> types.Part:
    """The user-message part that rejects ``request``. On resume the call is not
    run, and the model gets ``message`` (or a default that names the tool, never
    ``by``) as the tool's error result.

    Raises:
        ValueError: ``by`` is empty, or ``request`` is not a DDK approval request.
    """
    decision: dict[str, Any] = {"by": _who(by)}
    if message is not None:
        decision["message"] = message
    return _response(request, decision, confirmed=False)


def _kind(call: Any) -> object:
    confirmation = (getattr(call, "args", None) or {}).get("toolConfirmation")
    payload = confirmation.get("payload") if isinstance(confirmation, Mapping) else None
    return payload.get("kind") if isinstance(payload, Mapping) else None


def _response(request: Any, decision: dict[str, Any], *, confirmed: bool) -> types.Part:
    if getattr(request, "name", None) != _CONFIRMATION_CALL or _kind(request) != APPROVAL_KIND:
        raise ValueError("not a DDK approval request; use pending_approvals(events) to find them")
    from google.genai import types

    payload = {**request.args["toolConfirmation"]["payload"], **decision}
    return types.Part(
        function_response=types.FunctionResponse(
            id=request.id,
            name=_CONFIRMATION_CALL,
            response={"confirmed": confirmed, "payload": payload},
        )
    )


def _who(by: str) -> str:
    if not isinstance(by, str) or not by.strip():
        raise ValueError(f"by must name who decided, not {by!r}")
    return by.strip()


def _decision(confirmation: Any, approval: Approval) -> tuple[bool, str, str | None] | None:
    """``(approved, by, message)`` from ADK's ``ToolConfirmation``, or ``None``
    when it is not :func:`approve` or :func:`reject`'s answer to ``approval``."""
    payload = getattr(confirmation, "payload", None)
    approved = getattr(confirmation, "confirmed", None)
    if not isinstance(payload, Mapping) or not isinstance(approved, bool):
        return None
    if payload.get("kind") != APPROVAL_KIND or payload.get("approval_id") != approval.id:
        return None
    by, message = payload.get("by"), payload.get("message")
    if isinstance(by, str) and by.strip() and (message is None or isinstance(message, str)):
        return approved, by.strip(), message
    return None


def _requested(call_id: str, approval: Approval, events: Iterable[Any]) -> Mapping[str, Any] | None:
    """The payload of the pause the agent recorded in the session for
    ``approval``, or ``None``. A user message cannot carry one: ADK stores a
    requested confirmation on the agent's own event."""
    for event in events:
        if getattr(event, "author", "user") == "user":
            continue
        actions = getattr(event, "actions", None)
        asked = (getattr(actions, "requested_tool_confirmations", None) or {}).get(call_id)
        payload = (
            asked.get("payload") if isinstance(asked, Mapping) else getattr(asked, "payload", None)
        )
        if (
            isinstance(payload, Mapping)
            and payload.get("kind") == APPROVAL_KIND
            and payload.get("approval_id") == approval.id
        ):
            return payload
    return None


def _refusal(
    call_id: str,
    approval: Approval,
    decision: tuple[bool, str, str | None] | None,
    requested: Mapping[str, Any] | None,
    events: Iterable[Any],
) -> str | None:
    """Why the resumed call must not run, for the model, or ``None``."""
    tool = approval.tool
    if decision is None:
        return (
            f"This call of {tool!r} was not run: its confirmation is not an "
            "approve() or reject() answer to its approval request."
        )
    if requested is None:
        return f"This call of {tool!r} was not run: no approval of it was requested."
    placeholder = _awaiting(approval)
    for event in events:
        for response in event.get_function_responses() or ():
            if response.id == call_id and response.response != placeholder:
                return f"This call of {tool!r} was not run again: it was already answered."
    return None


def _awaiting(approval: Approval) -> dict[str, Any]:
    """The tool result a paused call answers with."""
    return {"status": "awaiting approval", "approval_id": approval.id}

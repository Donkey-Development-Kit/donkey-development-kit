"""The typed-refusal bridge sees inside exception groups (#950, ADR 0002).

A refusal raised in an ``asyncio.TaskGroup`` (or anyio) leaves the block
wrapped in an ``ExceptionGroup``. The bridge's rule, recorded in ADR 0002:

- every leaf translates to the same :class:`DonkeyError` class: the group
  collapses to that typed error (the first leaf's, depth first);
- otherwise, when any leaf translates: the group is rebuilt with the same
  shape and each translatable leaf replaced by its typed error, so
  ``except* PIIDetected`` works;
- no leaf translates: the group propagates unchanged.

``ExceptionGroup`` is builtin from Python 3.11, so the module is skipped below
it. The SDK-shaped errors are the same hand-built duck types as
``test_refusal_bridge.py``.
"""

from __future__ import annotations

import asyncio
import builtins
import sys
import traceback
import types
from typing import Any

import pytest

if sys.version_info < (3, 11):  # pragma: no cover - CI runs 3.11+
    pytest.skip("ExceptionGroup is builtin from Python 3.11", allow_module_level=True)

from test_refusal_bridge import (
    StatusError,
    _connection_error_from,
    _status_error,
    _unavailable,
)

from donkey_kit import (
    Donkey,
    DonkeyConfig,
    DonkeyError,
    GatewayUnavailable,
    PIIDetected,
    TypedRefusals,
    typed_refusals,
)
from donkey_kit.core.refusals import translate

# Builtin from 3.11; looked up so the module still parses under the py310 lint target.
ExceptionGroup: Any = builtins.ExceptionGroup  # type: ignore[attr-defined]
BaseExceptionGroup: Any = builtins.BaseExceptionGroup  # type: ignore[attr-defined]


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy", llm_proxy_client_id="cid", llm_proxy_client_secret="cs"
    )


async def _raise(exc: BaseException) -> None:
    await asyncio.sleep(0)
    raise exc


def _leaves(group: Any) -> list[BaseException]:
    out: list[BaseException] = []
    for exc in group.exceptions:
        out.extend(_leaves(exc) if isinstance(exc, BaseExceptionGroup) else [exc])
    return out


# --- translate(): a uniform group stands for its typed error ------------------


def test_translate_collapses_a_uniform_group() -> None:
    typed = _unavailable()
    group = ExceptionGroup("tasks", [_connection_error_from(typed)])
    assert translate(group) is typed


def test_translate_of_a_mixed_group_is_none() -> None:
    group = ExceptionGroup("tasks", [_status_error(), ValueError("boom")])
    assert translate(group) is None


def test_translate_of_a_group_with_different_refusal_classes_is_none() -> None:
    group = ExceptionGroup("tasks", [_status_error(), _connection_error_from(_unavailable())])
    assert translate(group) is None


# --- TaskGroup: the acceptance scenario ---------------------------------------


async def test_a_refusal_in_a_task_group_reaches_the_caller_typed() -> None:
    err = _status_error()
    with pytest.raises(PIIDetected) as excinfo:
        async with typed_refusals():
            async with asyncio.TaskGroup() as tg:
                tg.create_task(_raise(err))
    assert excinfo.value.framework_error is err
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


async def test_a_refusal_in_a_task_group_inside_donkey_run_is_typed() -> None:
    typed = _unavailable()
    with pytest.raises(GatewayUnavailable) as excinfo:
        async with Donkey(_cfg()).run(id="ticket-1"):
            async with asyncio.TaskGroup() as tg:
                tg.create_task(_raise(_connection_error_from(typed)))
                tg.create_task(asyncio.sleep(10))  # cancelled by the failing sibling
    assert excinfo.value is typed
    # A transport-raised typed error keeps its own cause, as outside a group.
    assert excinfo.value.__cause__ is typed.__cause__


async def test_a_governed_coroutine_types_a_task_group_refusal() -> None:
    donkey = Donkey(_cfg())

    @donkey.governed
    async def handler() -> None:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_raise(_status_error()))

    with pytest.raises(PIIDetected):
        await handler()


async def test_several_tasks_refused_alike_collapse_to_the_first() -> None:
    first, second = _status_error(), _status_error()
    with pytest.raises(PIIDetected) as excinfo, typed_refusals():
        raise ExceptionGroup("tasks", [first, second])
    assert excinfo.value.framework_error is first


async def test_mixed_task_failures_keep_the_group_with_typed_leaves() -> None:
    bug = ValueError("boom")
    with pytest.raises(ExceptionGroup) as excinfo:
        async with typed_refusals():
            async with asyncio.TaskGroup() as tg:
                tg.create_task(_raise(_status_error()))
                tg.create_task(_raise(bug))
    leaves = _leaves(excinfo.value)
    assert {type(leaf) for leaf in leaves} == {PIIDetected, ValueError}
    assert bug in leaves  # a leaf the bridge cannot type is the same object


def test_except_star_catches_the_typed_leaf_of_a_mixed_group() -> None:
    # ``except* T`` matches with ``group.split(T)``; the py310 lint target cannot
    # parse ``except*`` itself, so the test drives the same split.
    with pytest.raises(ExceptionGroup) as excinfo, typed_refusals():
        raise ExceptionGroup("tasks", [_status_error(), ValueError("boom")])
    caught, rest = excinfo.value.split(PIIDetected)
    assert [type(e) for e in caught.exceptions] == [PIIDetected]
    assert [type(e) for e in rest.exceptions] == [ValueError]


def test_different_refusal_classes_stay_a_group_of_typed_leaves() -> None:
    typed = _unavailable()
    with pytest.raises(ExceptionGroup) as excinfo, typed_refusals():
        raise ExceptionGroup("tasks", [_status_error(), _connection_error_from(typed)])
    leaves = _leaves(excinfo.value)
    assert isinstance(leaves[0], PIIDetected)
    assert leaves[1] is typed


# --- Nested groups ------------------------------------------------------------


def test_a_uniform_nested_group_collapses() -> None:
    first = _status_error()
    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [first]), _status_error()])
    with pytest.raises(PIIDetected) as excinfo, typed_refusals():
        raise nested
    assert excinfo.value.framework_error is first


def test_a_mixed_nested_group_keeps_its_shape() -> None:
    bug = KeyError("k")
    nested = ExceptionGroup(
        "outer", [ExceptionGroup("inner", [_status_error(), bug]), ValueError("v")]
    )
    with pytest.raises(ExceptionGroup) as excinfo, typed_refusals():
        raise nested
    outer = excinfo.value
    assert outer is not nested
    assert outer.message == "outer"
    inner = outer.exceptions[0]
    assert isinstance(inner, ExceptionGroup)
    assert inner.message == "inner"
    assert isinstance(inner.exceptions[0], PIIDetected)
    assert inner.exceptions[1] is bug
    assert outer.exceptions[1] is nested.exceptions[1]


def test_an_untouched_subgroup_is_reused() -> None:
    clean = ExceptionGroup("clean", [ValueError("v")])
    nested = ExceptionGroup("outer", [clean, _status_error()])
    with pytest.raises(ExceptionGroup) as excinfo, typed_refusals():
        raise nested
    assert excinfo.value.exceptions[0] is clean


# --- What passes through unchanged ---------------------------------------------


def test_a_group_with_no_refusal_propagates_unchanged() -> None:
    group = ExceptionGroup("tasks", [ValueError("a"), KeyError("b")])
    with pytest.raises(ExceptionGroup) as excinfo, typed_refusals():
        raise group
    assert excinfo.value is group


def test_a_group_of_typed_errors_of_one_class_collapses_to_the_first() -> None:
    first = PIIDetected("blocked")
    with pytest.raises(PIIDetected) as excinfo, typed_refusals():
        raise ExceptionGroup("tasks", [first, PIIDetected("blocked again")])
    assert excinfo.value is first
    assert first.framework_error is None


def test_a_group_of_already_typed_errors_of_mixed_classes_is_unchanged() -> None:
    group = ExceptionGroup("tasks", [PIIDetected("blocked"), _unavailable()])
    with pytest.raises(ExceptionGroup) as excinfo, typed_refusals():
        raise group
    assert excinfo.value is group


def test_a_base_exception_leaf_is_kept_and_the_group_stays_a_base_group() -> None:
    interrupt = KeyboardInterrupt()
    group = BaseExceptionGroup("tasks", [_status_error(), interrupt])
    with pytest.raises(BaseExceptionGroup) as excinfo, typed_refusals():
        raise group
    assert not isinstance(excinfo.value, Exception)
    assert isinstance(excinfo.value.exceptions[0], PIIDetected)
    assert excinfo.value.exceptions[1] is interrupt


# --- The rebuilt group: metadata, chaining, rendering ---------------------------


def test_the_rebuilt_group_keeps_cause_notes_and_traceback() -> None:
    cause = RuntimeError("root")
    try:
        try:
            raise ExceptionGroup("tasks", [_status_error(), ValueError("boom")]) from cause
        except ExceptionGroup as eg:
            eg.add_note("while fanning out")
            raise
    except ExceptionGroup as original:
        group = original
    with pytest.raises(ExceptionGroup) as excinfo, typed_refusals():
        raise group
    rebuilt = excinfo.value
    assert rebuilt.__cause__ is cause
    assert rebuilt.__notes__ == ["while fanning out"]
    # The original frames are kept ahead of the bridge's own.
    frames = traceback.extract_tb(rebuilt.__traceback__)
    assert any(f.name == "test_the_rebuilt_group_keeps_cause_notes_and_traceback" for f in frames)


def test_a_rebuilt_group_does_not_render_the_framework_error() -> None:
    """The framework error's message repeats the blocked values; neither the
    rebuilt group's traceback nor its implicit context may bring it back."""
    with pytest.raises(ExceptionGroup) as excinfo, typed_refusals():
        raise ExceptionGroup("tasks", [_status_error(), ValueError("boom")])
    assert excinfo.value.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(excinfo.value))
    assert "PIIDetected" in rendered
    assert "blocked: EMAIL" not in rendered


def test_a_collapsed_group_does_not_render_the_framework_error() -> None:
    with pytest.raises(PIIDetected) as excinfo, typed_refusals():
        raise ExceptionGroup("tasks", [_status_error()])
    rendered = "".join(traceback.format_exception(excinfo.value))
    assert "blocked: EMAIL" not in rendered


def test_framework_error_is_set_on_each_translated_leaf() -> None:
    err = _status_error()
    with pytest.raises(ExceptionGroup) as excinfo, typed_refusals():
        raise ExceptionGroup("tasks", [err, ValueError("boom")])
    leaf = excinfo.value.exceptions[0]
    assert isinstance(leaf, PIIDetected)
    assert leaf.framework_error is err


# --- Translators, sync forms, and the backport ----------------------------------


class Wrapped(Exception):
    """A framework wrapper with no HTTP shape, unwrapped by a translator."""

    def __init__(self, typed: DonkeyError) -> None:
        super().__init__("wrapped")
        self.typed = typed


def test_translators_are_applied_to_each_leaf() -> None:
    typed = PIIDetected("blocked")
    bridge = TypedRefusals(lambda: (lambda e: e.typed if isinstance(e, Wrapped) else None,))
    with pytest.raises(PIIDetected) as excinfo, bridge:
        raise ExceptionGroup("tasks", [Wrapped(typed)])
    assert excinfo.value is typed


def test_sync_run_types_a_group() -> None:
    with pytest.raises(PIIDetected), Donkey(_cfg()).run():
        raise ExceptionGroup("tasks", [_status_error()])


def test_run_with_typed_refusals_off_passes_the_group() -> None:
    group = ExceptionGroup("tasks", [_status_error()])
    with pytest.raises(ExceptionGroup) as excinfo, Donkey(_cfg()).run(typed_refusals=False):
        raise group
    assert excinfo.value is group
    assert isinstance(excinfo.value.exceptions[0], StatusError)


def test_the_exceptiongroup_backport_is_recognised_once_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Python 3.10 anyio raises the ``exceptiongroup`` backport's class. The
    bridge recognises it once the backport is imported, without importing it."""

    class BackportGroup(Exception):
        def __init__(self, message: str, excs: list[BaseException]) -> None:
            super().__init__(message)
            self.message = message
            self.exceptions = tuple(excs)

        def derive(self, excs: list[BaseException]) -> BackportGroup:
            return BackportGroup(self.message, excs)

    backport = types.ModuleType("exceptiongroup")
    backport.BaseExceptionGroup = BackportGroup  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "exceptiongroup", backport)

    with pytest.raises(PIIDetected), typed_refusals():
        raise BackportGroup("tasks", [_status_error()])
    with pytest.raises(BackportGroup) as excinfo, typed_refusals():
        raise BackportGroup("tasks", [_status_error(), ValueError("boom")])
    assert isinstance(excinfo.value.exceptions[0], PIIDetected)


# --- End to end: the simulator behind a real openai client ----------------------


async def test_a_simulated_refusal_in_a_task_group_inside_donkey_run_is_typed() -> None:
    """The acceptance scenario on the real wire: two concurrent calls through
    ``donkey.llm.client()`` both refused inside an ``asyncio.TaskGroup`` in
    ``donkey.run()`` reach the caller as one :class:`PIIDetected`."""
    openai = pytest.importorskip("openai")
    donkey = Donkey(
        DonkeyConfig(
            llm_proxy_url="https://proxy.example.internal/",
            llm_proxy_client_id="proxy-client-id",
            llm_proxy_client_secret="proxy-client-secret",
        )
    )
    try:
        client = donkey.llm.client()

        async def ask() -> None:
            await client.chat.completions.create(
                model="m", messages=[{"role": "user", "content": "hi"}]
            )

        with donkey.simulate(PIIDetected), pytest.raises(PIIDetected) as excinfo:
            async with donkey.run(id="ticket-1"):
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(ask())
                    tg.create_task(ask())
    finally:
        await donkey.aclose()
    assert isinstance(excinfo.value.framework_error, openai.APIStatusError)
    assert "john.doe@example.com" not in "".join(traceback.format_exception(excinfo.value))

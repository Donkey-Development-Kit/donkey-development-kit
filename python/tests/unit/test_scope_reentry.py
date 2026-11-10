"""One scope object entered by two tasks or threads at once (#1484).

``donkey.as_user()``, ``donkey.run()``, ``donkey.attribution()`` and
``donkey.cache()`` keep their contextvar reset tokens per entry, not on the
object. So a scope built once and entered concurrently restores each entry's
own state on exit, in either exit order: nothing stays bound after the block
and no ``ValueError`` about a token from a different context is raised. Nested
re-entry of one object on one task restores in LIFO order too, and an
out-of-order manual exit drops only its own entry (#1674).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from typing import Any

import pytest

from donkey_kit import CacheControls
from donkey_kit.core._scope_entry import _entries, pop_entry, push_entry
from donkey_kit.core.cachecontrol import CacheScope, current_cache_controls
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.correlation import (
    AttributionScope,
    RunScope,
    current_business_group,
    current_correlation_id,
)
from donkey_kit.core.identity import UserScope, current_user


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        client_id="cp-id",
        client_secret="cp-secret-value",
        base_url="https://anypoint.example.internal",
        llm_proxy_url="https://gw.example.internal/openai-sdk/",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )


# (name, scope factory, reader of the value the scope binds)
_SCOPES: list[tuple[str, Callable[[], Any], Callable[[], object]]] = [
    ("as_user", lambda: UserScope(_cfg(), "token-alice"), current_user),
    ("run", lambda: RunScope("run-1"), current_correlation_id),
    ("attribution", lambda: AttributionScope(business_group="bg-1"), current_business_group),
    ("cache", lambda: CacheScope(CacheControls(skip=True)), current_cache_controls),
]
_IDS = [name for name, _, _ in _SCOPES]


@pytest.mark.parametrize(("name", "make", "read"), _SCOPES, ids=_IDS)
@pytest.mark.parametrize(
    "first_exits_first", [True, False], ids=["first-exits-first", "first-exits-last"]
)
async def test_one_object_entered_by_two_tasks(
    name: str, make: Callable[[], Any], read: Callable[[], object], first_exits_first: bool
) -> None:
    scope = make()
    first_in = asyncio.Event()
    second_in = asyncio.Event()
    first_out = asyncio.Event()
    after: dict[str, object] = {}

    async def first() -> None:
        async with scope:
            assert read() is not None
            first_in.set()
            await second_in.wait()
            if not first_exits_first:
                await first_out.wait()
        after["first"] = read()
        first_out.set()

    async def second() -> None:
        await first_in.wait()
        async with scope:
            assert read() is not None
            second_in.set()
            if first_exits_first:
                await first_out.wait()
        after["second"] = read()
        first_out.set()

    await asyncio.wait_for(asyncio.gather(first(), second()), timeout=5)
    assert after == {"first": None, "second": None}
    assert read() is None


@pytest.mark.parametrize(("name", "make", "read"), _SCOPES, ids=_IDS)
@pytest.mark.parametrize(
    "first_exits_first", [True, False], ids=["first-exits-first", "first-exits-last"]
)
def test_one_object_entered_by_two_threads(
    name: str, make: Callable[[], Any], read: Callable[[], object], first_exits_first: bool
) -> None:
    scope = make()
    first_in = threading.Event()
    second_in = threading.Event()
    first_out = threading.Event()

    def first() -> object:
        with scope:
            assert read() is not None
            first_in.set()
            assert second_in.wait(5)
            if not first_exits_first:
                assert first_out.wait(5)
        first_out.set()
        return read()

    def second() -> object:
        assert first_in.wait(5)
        with scope:
            assert read() is not None
            second_in.set()
            if first_exits_first:
                assert first_out.wait(5)
        first_out.set()
        return read()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(first), pool.submit(second)
        assert (a.result(timeout=10), b.result(timeout=10)) == (None, None)
    assert read() is None


@pytest.mark.parametrize(("name", "make", "read"), _SCOPES, ids=_IDS)
def test_nested_reentry_on_one_task_restores_in_order(
    name: str, make: Callable[[], Any], read: Callable[[], object]
) -> None:
    scope = make()
    with scope:
        outer = read()
        with scope:
            assert read() is not None
        assert read() is outer
    assert read() is None


def test_run_scope_restores_the_outer_run_when_reused_inside_it() -> None:
    inner = RunScope("inner")
    with RunScope("outer"), inner:
        assert current_correlation_id() == "inner"
        with inner:
            assert current_correlation_id() == "inner"
        assert current_correlation_id() == "inner"
    assert current_correlation_id() is None


# --- out-of-order exit (#1674) -----------------------------------------------

_v: ContextVar[int | None] = ContextVar("test_scope_entry_v", default=None)
_w: ContextVar[int | None] = ContextVar("test_scope_entry_w", default=None)


def test_out_of_order_pop_leaves_no_stale_entry() -> None:
    a, b = object(), object()
    push_entry(a, [_v.set(1)])
    push_entry(b, [_w.set(2)])
    pop_entry(a)
    assert (_v.get(), _w.get()) == (None, 2)
    pop_entry(b)
    assert (_v.get(), _w.get()) == (None, None)
    assert _entries.get() == ()
    # A second exit of A is the documented no-op, not a spent-token RuntimeError.
    pop_entry(a)
    pop_entry(b)
    assert _entries.get() == ()


def test_out_of_order_pop_of_three_entries_keeps_the_rest() -> None:
    a, b, c = object(), object(), object()
    push_entry(a, [_v.set(1)])
    push_entry(b, [_w.set(2)])
    push_entry(c, [])
    pop_entry(b)
    assert [e.owner for e in _entries.get()] == [a, c]
    pop_entry(c)
    assert [e.owner for e in _entries.get()] == [a]
    pop_entry(a)
    assert _entries.get() == ()
    assert (_v.get(), _w.get()) == (None, None)


def test_out_of_order_manual_exit_of_public_scopes() -> None:
    # The reachable path: manual __enter__/__exit__ in non-LIFO order.
    user = UserScope(_cfg(), "token-alice")
    attribution = AttributionScope(business_group="bg-1")
    user.__enter__()
    attribution.__enter__()
    user.__exit__(None, None, None)
    attribution.__exit__(None, None, None)
    assert current_user() is None
    assert current_business_group() is None
    assert _entries.get() == ()
    user.__exit__(None, None, None)  # no-op
    attribution.__exit__(None, None, None)  # no-op
    assert _entries.get() == ()

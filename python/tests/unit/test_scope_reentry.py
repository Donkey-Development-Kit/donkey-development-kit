"""One scope object entered by two tasks or threads at once (#1484).

``donkey.as_user()``, ``donkey.run()``, ``donkey.attribution()`` and
``donkey.cache()`` keep their contextvar reset tokens per entry, not on the
object. So a scope built once and entered concurrently restores each entry's
own state on exit, in either exit order: nothing stays bound after the block
and no ``ValueError`` about a token from a different context is raised. Nested
re-entry of one object on one task restores in LIFO order too.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from donkey_kit import CacheControls
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

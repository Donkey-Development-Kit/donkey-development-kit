"""Static contract for the headline public API under a downstream ``mypy --strict`` (#716).

Never executed: mypy checks it (``files`` in ``pyproject.toml``). Each
``assert_type`` pins a public annotation as a user sees it. Each negative case is
a ``# type: ignore[<code>]`` on a call that must fail type checking; strict mode's
``warn_unused_ignores`` turns the ignore into an error the moment the call stops
failing, so a loosened annotation cannot pass silently.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from openai import AsyncOpenAI, OpenAI
from typing_extensions import assert_type

from donkey_kit import CostTags, Donkey, DonkeyConfig
from donkey_kit.core.cachecontrol import CacheScope
from donkey_kit.core.lastcall import LastCall
from donkey_kit.core.telemetry import RunScope
from donkey_kit.integrations._base import Adapter
from donkey_kit.integrations.agent_framework import AgentFrameworkAdapter
from donkey_kit.integrations.langgraph import LangGraphAdapter

# --- @governed keeps the decorated callable's signature, in both forms -----------


async def check_governed(donkey: Donkey) -> None:
    @donkey.governed
    def bare_sync(ticket: str, *, retries: int = 1) -> int:
        return retries

    @donkey.governed(team="support", project="triage")
    def keyword_sync(ticket: str, *, retries: int = 1) -> int:
        return retries

    @donkey.governed
    async def bare_async(ticket: str) -> str:
        return ticket

    @donkey.governed(team="support")
    async def keyword_async(ticket: str) -> str:
        return ticket

    assert_type(bare_sync("t", retries=2), int)
    assert_type(keyword_sync("t", retries=2), int)
    assert_type(await bare_async("t"), str)
    assert_type(await keyword_async("t"), str)

    bare_sync(1)  # type: ignore[arg-type]
    keyword_sync("t", retry=2)  # type: ignore[call-arg]
    await bare_async()  # type: ignore[call-arg]
    await keyword_async(1)  # type: ignore[arg-type]

    decorator = donkey.governed(team="support")
    governed_fn: Callable[[str], Awaitable[str]] = decorator(keyword_async)
    del governed_fn


# --- run() / cache() / last_call / openai() ---------------------------------------


async def check_scopes(donkey: Donkey) -> None:
    assert_type(donkey.run(team="support"), RunScope)
    async with donkey.run(id="ticket-1") as run_id:
        assert_type(run_id, str)
    with donkey.run() as sync_run_id:
        assert_type(sync_run_id, str)

    assert_type(donkey.cache(skip=True), CacheScope)
    donkey.cache(ttl="60")  # type: ignore[arg-type]

    assert_type(donkey.last_call, LastCall)
    assert_type(donkey.last_call.total_tokens, int | None)

    assert_type(donkey.openai(), AsyncOpenAI)
    assert_type(donkey.openai(sync=True), OpenAI)
    assert_type(donkey.llm.client(), AsyncOpenAI)


# --- adapters: the shared contract on the base, native objects on each ------------


def generic_connection_kwargs(adapter: Adapter) -> dict[str, Any]:
    # Code written against the base sees the contract every adapter shares.
    assert_type(adapter.extra, str)
    assert_type(adapter.observes_last_call, bool)
    return adapter.connection_kwargs()


def check_adapters(donkey: Donkey, cfg: DonkeyConfig) -> None:
    assert_type(donkey.langgraph, LangGraphAdapter)
    assert_type(donkey.agent_framework, AgentFrameworkAdapter)
    generic_connection_kwargs(donkey.langgraph)
    generic_connection_kwargs(donkey.agent_framework)
    # An adapter name the lazy registry resolves at runtime still types as Adapter.
    assert_type(donkey.some_future_framework, Adapter)
    assert_type(donkey.some_future_framework.connection_kwargs(), dict[str, Any])

    Adapter(cfg, donkey._http)  # type: ignore[abstract]


# --- config helpers reject typos ---------------------------------------------------


def check_config(cfg: DonkeyConfig) -> None:
    assert_type(cfg.with_overrides(timeout_s=1.0), DonkeyConfig)
    assert_type(cfg.with_overrides(cost=CostTags(team="support"), telemetry=False), DonkeyConfig)
    assert_type(cfg.validated(need="llm"), DonkeyConfig)
    assert_type(cfg.validated(), DonkeyConfig)

    cfg.with_overrides(timout_s=1.0)  # type: ignore[call-arg]
    cfg.with_overrides(timeout_s="1.0")  # type: ignore[arg-type]
    cfg.with_overrides(region="mars")  # type: ignore[arg-type]
    cfg.validated(need="llmm")  # type: ignore[arg-type]
    cfg.missing_fields(need="control-plane")  # type: ignore[arg-type]

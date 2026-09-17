"""``@donkey.governed`` and ``@donkey.tool`` — the one-line on-ramps (#200).

``governed`` wraps sync/async callables in a ``donkey.run()`` scope; ``tool``
records a marker + registry entry without changing call behaviour, and rejects
an undescribed tool.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from donkey_kit import Donkey, DonkeyConfig, ToolSpec, registered_tools
from donkey_kit.core.telemetry import current_correlation_id, current_cost_tags
from donkey_kit.core.toolspec import TOOL_MARKER, _clear_registry, tool_spec


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )


@pytest.fixture(autouse=True)
def _isolate_registry() -> Iterator[None]:
    """Each test sees an empty process-global tool registry."""
    _clear_registry()
    yield
    _clear_registry()


# --- @donkey.governed -------------------------------------------------------
def test_governed_wraps_sync_callable_in_a_run_scope() -> None:
    fab = Donkey(_cfg())
    seen: list[str | None] = []

    @fab.governed()
    def handle() -> str:
        seen.append(current_correlation_id())
        return "ok"

    assert handle() == "ok"
    assert seen[0] is not None  # a run id was bound inside the body
    assert current_correlation_id() is None  # and restored on exit


async def test_governed_wraps_async_callable_in_a_run_scope() -> None:
    fab = Donkey(_cfg())
    seen: list[str | None] = []

    @fab.governed()
    async def handle() -> str:
        seen.append(current_correlation_id())
        return "ok"

    assert await handle() == "ok"
    assert seen[0] is not None
    assert current_correlation_id() is None


def test_governed_usable_bare_without_parentheses() -> None:
    """``@donkey.governed`` (no call) wraps just like ``@donkey.governed()``."""
    fab = Donkey(_cfg())
    seen: list[str | None] = []

    @fab.governed
    def handle() -> None:
        seen.append(current_correlation_id())

    handle()
    assert seen[0] is not None
    assert current_correlation_id() is None


def test_governed_each_call_opens_a_fresh_run() -> None:
    """No fixed id is pinned across calls — each invocation is a run of one, so
    two calls carry two distinct correlation ids (#200)."""
    fab = Donkey(_cfg())
    ids: list[str | None] = []

    @fab.governed()
    def handle() -> None:
        ids.append(current_correlation_id())

    handle()
    handle()
    assert ids[0] is not None and ids[1] is not None
    assert ids[0] != ids[1]


def test_governed_binds_per_run_cost_tags() -> None:
    fab = Donkey(_cfg())
    seen: list[str | None] = []

    @fab.governed(team="support", project="triage")
    def handle() -> None:
        tags = current_cost_tags()
        seen.append(tags.team if tags else None)
        seen.append(tags.project if tags else None)

    handle()
    assert seen == ["support", "triage"]
    assert current_cost_tags() is None  # restored on exit


def test_governed_preserves_name_and_docstring() -> None:
    fab = Donkey(_cfg())

    @fab.governed()
    def handle() -> None:
        """Handle a ticket."""

    assert handle.__name__ == "handle"
    assert handle.__doc__ == "Handle a ticket."


def test_governed_propagates_the_wrapped_exception() -> None:
    """The scope closes and the caller's exception is not swallowed."""
    fab = Donkey(_cfg())

    @fab.governed()
    def boom() -> None:
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError, match="nope"):
        boom()
    assert current_correlation_id() is None


# --- @donkey.tool -----------------------------------------------------------
def test_tool_records_name_signature_and_docstring() -> None:
    @Donkey.tool
    def lookup_crm(customer_id: str) -> dict[str, str]:
        """Look a customer up in the CRM."""
        return {"id": customer_id}

    (spec,) = registered_tools()
    assert isinstance(spec, ToolSpec)
    assert spec.name == "lookup_crm"
    assert spec.docstring == "Look a customer up in the CRM."
    assert list(spec.signature.parameters) == ["customer_id"]
    assert spec.is_async is False


def test_tool_records_async_flag() -> None:
    @Donkey.tool
    async def lookup_crm(customer_id: str) -> dict[str, str]:
        """Look a customer up in the CRM."""
        return {"id": customer_id}

    (spec,) = registered_tools()
    assert spec.is_async is True


def test_tool_does_not_change_call_behaviour() -> None:
    """The decorator returns the same callable — calling it is unaffected."""

    def lookup_crm(customer_id: str) -> dict[str, str]:
        """Look a customer up."""
        return {"id": customer_id}

    decorated = Donkey.tool(lookup_crm)
    assert decorated is lookup_crm  # identity preserved
    assert decorated("c-1") == {"id": "c-1"}


def test_tool_attaches_an_introspectable_marker() -> None:
    @Donkey.tool
    def lookup_crm() -> None:
        """Look up."""

    spec = tool_spec(lookup_crm)
    assert spec is not None
    assert getattr(lookup_crm, TOOL_MARKER) is spec
    assert spec.name == "lookup_crm"


def test_tool_without_docstring_is_flagged() -> None:
    """An undescribed tool is rejected at decoration time (#200)."""
    with pytest.raises(ValueError, match="requires a docstring"):

        @Donkey.tool
        def lookup_crm() -> None:  # no docstring
            return None

    assert registered_tools() == ()  # nothing recorded for the rejected tool


def test_tool_with_blank_docstring_is_flagged() -> None:
    with pytest.raises(ValueError, match="requires a docstring"):

        @Donkey.tool
        def lookup_crm() -> None:
            """   """


def test_registered_tools_reflects_decoration_order() -> None:
    @Donkey.tool
    def first() -> None:
        """First."""

    @Donkey.tool
    def second() -> None:
        """Second."""

    assert [s.name for s in registered_tools()] == ["first", "second"]

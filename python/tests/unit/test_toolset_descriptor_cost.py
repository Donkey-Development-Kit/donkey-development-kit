"""A ``ToolSet`` binding logs what its descriptors cost and warns when the set is
large (BG §2.7, #1257).

Every binding, CrewAI included, goes through ``ToolSet._plan``: it logs the exposed tool
count and the ``descriptor_tokens()`` estimate at debug level, and warns once
either passes ``tool_warn_count`` or ``tool_warn_tokens``. The defaults come
from published vendor guidance, cited in ``tools/session.py`` and on the
configuration page. ``list_tools`` is replaced so no server is needed, and the
framework modules are stand-ins: what is under test is the seam, not the
framework's MCP client.
"""

from __future__ import annotations

import logging
import sys
import types
from collections.abc import Mapping
from typing import Any

import pytest

from donkey_kit import DonkeyConfig
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import session as session_module
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.discovery import Discovery
from donkey_kit.tools.session import ToolSet

_AUTH = ClientIdAuth("cid", "secret")
_GOVERNED = GovernanceReport(governed=True, checks=[Check("deployed", True, "ok")])
_LOGGER = session_module.__name__


def _server(asset_id: str, tools: int, *, description: str = "") -> McpServerHandle:
    return McpServerHandle(
        ref=AssetRef("com.acme", asset_id, "1.0.0"),
        endpoint_url=f"https://gw.example.test/{asset_id}",
        governance=_GOVERNED,
        tool_descriptors=tuple(
            {"name": f"tool_{i}", "description": description, "inputSchema": {"type": "object"}}
            for i in range(tools)
        ),
    )


def _set(tools: int, *, description: str = "", limits: tuple[int, int] | None = None) -> ToolSet:
    if limits is None:
        return ToolSet([_server("hr", tools, description=description)], _auth=_AUTH)
    return ToolSet([_server("hr", tools, description=description)], _auth=_AUTH, _limits=limits)


def _messages(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == _LOGGER and r.levelno == level]


# ---- the defaults ---------------------------------------------------------------


def test_the_defaults_are_the_published_vendor_guidance() -> None:
    # OpenAI: "Aim for fewer than 20 functions"; Gemini: "Keep active set to
    # 10-20 tools maximum"; Anthropic: use tool search once "Your tool
    # definitions consume more than 10k tokens". See tools/session.py.
    cfg = DonkeyConfig(telemetry=False)
    assert cfg.tool_warn_count == 20
    assert cfg.tool_warn_tokens == 10_000


# ---- debug log on every bind ----------------------------------------------------


async def test_a_bind_logs_the_count_and_the_token_estimate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tools = _set(3)
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        await tools._plan("adk()", per_tool=False)
    expected = (
        f"adk() binds 3 tool descriptor(s), about {tools.descriptor_tokens()} "
        "descriptor tokens per model call"
    )
    assert expected in _messages(caplog, logging.DEBUG)
    assert _messages(caplog, logging.WARNING) == []


async def test_the_log_counts_what_filter_left(caplog: pytest.LogCaptureFixture) -> None:
    tools = _set(5).filter(allow=["tool_0", "tool_1"])
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        await tools._plan("langgraph()", per_tool=True)
    assert any(
        m.startswith("langgraph() binds 2 tool descriptor(s), about ")
        for m in _messages(caplog, logging.DEBUG)
    )


# ---- the warning ------------------------------------------------------------------


async def test_more_tools_than_the_count_threshold_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _set(21)._plan("adk()", per_tool=False)
    (warning,) = _messages(caplog, logging.WARNING)
    assert "adk() binds 21 tool descriptors" in warning
    assert "more than tool_warn_count=20" in warning
    assert "tool_warn_tokens" not in warning
    assert "filter()" in warning


async def test_the_count_threshold_itself_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _set(20)._plan("adk()", per_tool=False)
        await _set(1)._plan("adk()", per_tool=False)
    assert _messages(caplog, logging.WARNING) == []


async def test_more_tokens_than_the_token_threshold_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tools = _set(2, description="x" * 30_000)
    assert tools.descriptor_tokens() > 10_000
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await tools._plan("openai()", per_tool=False)
    (warning,) = _messages(caplog, logging.WARNING)
    assert f"about {tools.descriptor_tokens()} descriptor tokens per model call" in warning
    assert "more than tool_warn_tokens=10000" in warning
    assert "tool_warn_count" not in warning


async def test_the_token_threshold_itself_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tokens = _set(1, description="x" * 4_000).descriptor_tokens()
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _set(1, description="x" * 4_000, limits=(20, tokens))._plan("adk()", per_tool=False)
    assert _messages(caplog, logging.WARNING) == []
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _set(1, description="x" * 4_000, limits=(20, tokens - 1))._plan(
            "adk()", per_tool=False
        )
    assert len(_messages(caplog, logging.WARNING)) == 1


async def test_both_thresholds_are_named_in_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _set(30, description="x" * 2_000)._plan("adk()", per_tool=False)
    (warning,) = _messages(caplog, logging.WARNING)
    assert "more than tool_warn_count=20" in warning
    assert "more than tool_warn_tokens=10000" in warning


async def test_an_empty_bind_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await ToolSet([], _auth=_AUTH, _limits=(0, 0))._plan("adk()", per_tool=False)
    assert _messages(caplog, logging.WARNING) == []


# ---- the config override ----------------------------------------------------------


async def test_a_lower_threshold_from_config_warns_sooner(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _set(6, limits=(5, 10_000))._plan("adk()", per_tool=False)
    (warning,) = _messages(caplog, logging.WARNING)
    assert "more than tool_warn_count=5" in warning


async def test_zero_turns_a_threshold_off(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        await _set(50, description="x" * 2_000, limits=(0, 0))._plan("adk()", per_tool=False)
    assert _messages(caplog, logging.WARNING) == []
    # The cost is still logged.
    assert any("binds 50 tool descriptor(s)" in m for m in _messages(caplog, logging.DEBUG))


async def test_filter_and_approval_keep_the_thresholds(caplog: pytest.LogCaptureFixture) -> None:
    narrowed = (
        _set(10, limits=(3, 10_000)).filter(deny=["tool_0"]).require_approval(tools=["tool_1"])
    )
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await narrowed._plan("langgraph()", per_tool=True)
    (warning,) = _messages(caplog, logging.WARNING)
    assert "binds 9 tool descriptors" in warning
    assert "more than tool_warn_count=3" in warning


class _Registry:
    """One governed MCP server, ``hr``, for ``Discovery``."""

    async def search(self, **_: Any) -> list[AssetRef]:
        return [AssetRef("com.acme", "hr", "1.0.0")]

    async def explain(self, ref: AssetRef, **_: Any) -> GovernanceReport:
        return _GOVERNED

    async def resolve_mcp(self, ref: AssetRef) -> McpServerHandle:
        return McpServerHandle(ref=ref, endpoint_url="https://gw.example.test/hr")


@pytest.mark.parametrize(
    ("overrides", "env", "warns"),
    [
        ({}, {}, False),
        ({"tool_warn_count": 3}, {}, True),
        ({}, {"DONKEY_TOOL_WARN_COUNT": "3"}, True),
        ({}, {"DONKEY_TOOL_WARN_TOKENS": "5"}, True),
        ({"tool_warn_count": 0}, {"DONKEY_TOOL_WARN_COUNT": "3"}, False),
    ],
)
async def test_discover_binds_with_the_configured_thresholds(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    overrides: dict[str, int],
    env: dict[str, str],
    warns: bool,
) -> None:
    async def four_tools(url: str, headers: Mapping[str, str], *, server: str) -> Any:
        return tuple({"name": f"tool_{i}"} for i in range(4))

    monkeypatch.setattr(session_module, "list_tools", four_tools)
    for name in ("DONKEY_TOOL_WARN_COUNT", "DONKEY_TOOL_WARN_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    cfg = DonkeyConfig.resolve(telemetry=False, **overrides)  # type: ignore[arg-type]
    discovery = Discovery(_Registry(), cfg, auth=_AUTH)  # type: ignore[arg-type]
    tools = await discovery.discover(domain=None, tags=None, governed=True, auth=None)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await tools._plan("adk()", per_tool=False)
    assert bool(_messages(caplog, logging.WARNING)) is warns


@pytest.mark.parametrize(
    ("env", "value"), [("DONKEY_TOOL_WARN_COUNT", "-1"), ("DONKEY_TOOL_WARN_TOKENS", "lots")]
)
def test_an_invalid_threshold_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str
) -> None:
    from donkey_kit.core.errors import ConfigError

    monkeypatch.setenv(env, value)
    with pytest.raises(ConfigError, match=env):
        DonkeyConfig.resolve(telemetry=False)


# ---- every binding goes through the seam ------------------------------------------

# The modules each binding imports, with the names it takes from them. Stand-ins,
# so the binding reaches _plan() with no framework installed.
_FRAMEWORK_MODULES: dict[str, tuple[str, ...]] = {
    "langchain_mcp_adapters.client": ("MultiServerMCPClient",),
    "llama_index.core.tools": ("FunctionTool",),
    "llama_index.tools.mcp": ("BasicMCPClient", "McpToolSpec"),
    "anthropic.lib.tools": ("BetaAsyncFunctionTool",),
    "anthropic.lib.tools.mcp": ("async_mcp_tool",),
    "google.adk.tools.mcp_tool": ("McpToolset", "StreamableHTTPConnectionParams"),
    "strands.tools.mcp": ("MCPClient",),
    "agents.mcp": ("MCPServerStreamableHttp", "create_static_tool_filter"),
    "agent_framework": ("MCPStreamableHTTPTool",),
    "crewai_tools": ("MCPServerAdapter",),
}
_BINDINGS = ("langgraph", "llamaindex", "anthropic", "adk", "strands", "openai")
_BINDINGS += ("agent_framework", "crewai")


class _Stop(Exception):
    """Raised right after ``_plan()`` returns, before the framework class is used."""


@pytest.fixture
def frameworks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand-in modules for every binding's imports, restored after the test."""
    fakes: dict[str, types.ModuleType] = {}
    for dotted, names in _FRAMEWORK_MODULES.items():
        parts = dotted.split(".")
        for depth in range(1, len(parts) + 1):
            name = ".".join(parts[:depth])
            if name not in fakes:
                fakes[name] = types.ModuleType(name)
                fakes[name].__path__ = []
                monkeypatch.setitem(sys.modules, name, fakes[name])
        for attr in names:
            setattr(fakes[dotted], attr, object())


@pytest.mark.parametrize("method", _BINDINGS)
async def test_every_binding_logs_and_warns(
    method: str,
    frameworks: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    real_plan = ToolSet._plan

    async def plan_then_stop(self: ToolSet, binding: str, *, per_tool: bool) -> Any:
        await real_plan(self, binding, per_tool=per_tool)
        raise _Stop

    monkeypatch.setattr(ToolSet, "_plan", plan_then_stop)
    tools = _set(21)
    with caplog.at_level(logging.DEBUG, logger=_LOGGER), pytest.raises(_Stop):
        if method in {"anthropic", "crewai"}:
            async with getattr(tools, method)():
                pass
        else:
            await getattr(tools, method)()
    assert any(
        m.startswith(f"{method}() binds 21 tool descriptor(s)")
        for m in _messages(caplog, logging.DEBUG)
    )
    (warning,) = _messages(caplog, logging.WARNING)
    assert warning.startswith(f"{method}() binds 21 tool descriptors")


def test_the_binding_list_is_every_binding() -> None:
    bindings = {
        name
        for name in vars(ToolSet)
        if not name.startswith("_")
        and callable(getattr(ToolSet, name))
        and name
        not in {
            "filter",
            "descriptor_tokens",
            "require_approval",
            "describe",
            "wait_for_gateway_approval",
        }
    }
    assert bindings == set(_BINDINGS)

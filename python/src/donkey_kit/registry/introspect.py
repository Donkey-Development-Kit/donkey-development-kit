"""descriptor='auto' derivation (BG §2.5).

KEY INSIGHT (BG §2.5): do NOT write a type-hint→JSON-Schema converter. Every
framework already derived the schema from signatures/type-hints/docstrings; ask
the framework for the schema it already computed. Re-deriving would produce a
second, subtly-different schema from the one the model actually sees.

Modes, by fidelity (BG §2.5):
  * ``auto``        — object introspection (default): import the entrypoint, read
    already-computed schemas. Importing user code EXECUTES it → require an
    explicit entrypoint; tool definitions must be import-safe.
  * ``auto:live``   — highest fidelity: run the server, MCP initialize +
    tools/list. Captures dynamic registration. An in-process MCP server object
    only: the protocol runs over the MCP SDK's in-memory transport.
  * ``auto:static`` — AST only, lower fidelity: MUST emit a completeness warning
    on unresolved dynamic registration; never the default. Still blocked: an
    AST pass can name a tool but cannot produce its schema without the
    converter the key insight forbids (#1248).
  * ``auto:check``  — generate (as ``auto``), diff against a committed
    descriptor, fail on mismatch. The CI-friendly mode.

Every per-framework attribute read here is semi-public and WILL break on
upstream releases → each one is captured as a fixture
(``tests/fixtures/descriptor/``, by ``scripts/capture_descriptor_fixtures.py``)
and asserted in every adapter-contract leg and the nightly matrix (BG §2.5).
The reads are VERIFIED in docs/verified-apis.md §10; a tool object from any
other framework still raises ``_verify.blocked(...)``.

No framework is imported here: a tool is recognised by the package of a class
in its MRO plus the attribute §10 names, the same duck typing
``core/refusals.py`` uses, so the registry layer stays framework-free.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
from collections.abc import Callable, Iterable
from typing import Any

from ..core import _verify

__all__ = ["DerivationMode", "DescriptorMismatch", "derive_descriptor"]

DerivationMode = str  # "auto" | "auto:live" | "auto:static" | "auto:check"

_MODES = ("auto", "auto:live", "auto:static", "auto:check")

# One derived tool: the MCP tool-manifest triple (docs/verified-apis.md §11).
_Tool = dict[str, Any]


class DescriptorMismatch(ValueError):
    """``auto:check``: the derived descriptor differs from the committed one.

    Regenerate the committed file from the code (``auto``) if the change is
    intended, or fix the tool definition if it is not.
    """


async def derive_descriptor(
    entrypoint: str,
    *,
    mode: DerivationMode = "auto",
    against: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """Derive an MCP tool manifest from code (BG §2.5).

    ``entrypoint`` is ``"module:attribute"``. The attribute is a framework tool,
    an MCP server, a list or tuple of tools, or a zero-argument factory that
    returns one of those. Importing the module executes it, so tool
    definitions must be import-safe. The result is
    ``{"tools": [{"name", "description", "inputSchema"}, ...]}`` in the
    framework's own order, each schema exactly as the framework computed it.

    ``mode="auto:live"`` needs an MCP server and lists its tools over the MCP
    protocol (initialize + ``tools/list``) instead of reading the object.
    ``mode="auto:check"`` derives as ``auto`` and raises
    :class:`DescriptorMismatch` unless the result equals the JSON file at
    ``against``. ``mode="auto:static"`` is still blocked on verification.

    Raises ``NotImplementedError`` ("blocked on verification") for a tool
    object from a framework whose attributes are not verified
    (docs/verified-apis.md §10).
    """
    if mode not in _MODES:
        raise ValueError(f"mode must be one of {', '.join(_MODES)}, not {mode!r}.")
    if (mode == "auto:check") != (against is not None):
        raise ValueError("against= is required with mode='auto:check' and only with it.")
    if mode == "auto:static":
        raise _verify.blocked(
            "descriptor derivation mode 'auto:static' (docs/verified-apis.md §10): an AST "
            "pass cannot produce a tool's schema without re-deriving it, which BG §2.5 "
            "forbids (#1248). Use mode='auto'."
        )
    target = _resolve(entrypoint)
    if mode == "auto:live":
        if not _is_mcp_server(target):
            raise TypeError(
                f"mode='auto:live' needs an MCP server, but {entrypoint!r} is a "
                f"{_type_name(target)}. Framework tools have no protocol surface; use mode='auto'."
            )
        tools = await _live_mcp_tools(target)
    else:
        tools = await _describe(target)
    names = [tool["name"] for tool in tools]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(
            f"{entrypoint!r} defines more than one tool named {', '.join(duplicates)}."
        )
    descriptor: dict[str, object] = {"tools": tools}
    if against is not None:
        with open(against, encoding="utf-8") as fh:
            committed = json.load(fh)
        if committed != descriptor:
            raise DescriptorMismatch(
                f"The descriptor derived from {entrypoint!r} differs from {os.fspath(against)!r}. "
                "Regenerate the committed file with mode='auto' if the change is intended."
            )
    return descriptor


# --- entrypoint resolution ----------------------------------------------------


def _resolve(entrypoint: str) -> object:
    module_name, sep, attribute = entrypoint.partition(":")
    if not sep or not module_name or not attribute:
        raise ValueError(f"entrypoint must be 'module:attribute', not {entrypoint!r}.")
    target: object = importlib.import_module(module_name)
    for part in attribute.split("."):
        target = getattr(target, part)
    if _recognised(target) or not callable(target):
        return target
    if _required_parameters(target):
        raise TypeError(
            f"{entrypoint!r} is a {_type_name(target)} that takes arguments, not a framework "
            "tool or a zero-argument factory. Descriptor derivation reads the schema a "
            "framework already computed and never re-derives one from a plain function "
            "(BG §2.5): name a tool, an MCP server, a list of tools, or a factory for one."
        )
    return target()


def _required_parameters(func: Callable[..., object]) -> bool:
    try:
        parameters = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):  # a builtin with no signature: not a factory
        return True
    return any(
        p.default is p.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in parameters
    )


def _recognised(obj: object) -> bool:
    return isinstance(obj, (list, tuple)) or _is_mcp_server(obj) or _reader(obj) is not None


async def _describe(target: object) -> list[_Tool]:
    if _is_mcp_server(target):
        return [_mcp_tool(tool) for tool in await target.list_tools()]  # type: ignore[attr-defined]
    items: Iterable[object] = target if isinstance(target, (list, tuple)) else (target,)
    tools: list[_Tool] = []
    for item in items:
        reader = _reader(item)
        if reader is None:
            raise _unsupported(item)
        tools.append(reader(item))
    return tools


def _unsupported(obj: object) -> Exception:
    if isinstance(obj, dict) or not _framework_package(obj):
        return TypeError(
            f"{_type_name(obj)} is not a framework tool. Descriptor derivation reads the schema "
            "a framework already computed and never re-derives one from a plain function (BG §2.5)."
        )
    return _verify.blocked(
        f"descriptor-derivation attributes for {_type_name(obj)} (docs/verified-apis.md §10). "
        "Read the already-computed schema; never re-derive it (BG §2.5)."
    )


# --- per-framework readers (docs/verified-apis.md §10) -------------------------


def _mro_packages(obj: object) -> set[str]:
    return {cls.__module__.partition(".")[0] for cls in type(obj).__mro__}


def _from(obj: object, *prefixes: str) -> bool:
    modules = [cls.__module__ for cls in type(obj).__mro__]
    return any(m == p or m.startswith(p + ".") for m in modules for p in prefixes)


def _framework_package(obj: object) -> bool:
    return bool(_mro_packages(obj) - {"builtins", "abc", "typing", "pydantic", "__main__"})


def _tool(name: object, description: object, schema: object) -> _Tool:
    return {"name": name, "description": description, "inputSchema": schema}


def _json_schema(schema: Any) -> Any:
    # A pydantic model class carries its schema; a dict already is one.
    return schema if isinstance(schema, dict) else schema.model_json_schema()


def _langchain(tool: Any) -> _Tool:
    # tool_call_schema, not args_schema: it drops InjectedToolArg parameters,
    # which the model never sees.
    return _tool(tool.name, tool.description, _json_schema(tool.tool_call_schema))


def _strands(tool: Any) -> _Tool:
    spec = tool.tool_spec
    return _tool(spec["name"], spec["description"], spec["inputSchema"]["json"])


def _adk(tool: Any) -> _Tool:
    # _get_declaration() is private, and the only place ADK exposes the schema
    # it sends; the nightly matrix is what catches its next rename.
    declaration = tool._get_declaration()  # noqa: SLF001
    if declaration is None or declaration.parameters_json_schema is None:
        raise _verify.blocked(
            f"an ADK {_type_name(tool)} declaration without parameters_json_schema "
            "(docs/verified-apis.md §10)."
        )
    return _tool(declaration.name, declaration.description, declaration.parameters_json_schema)


def _llamaindex(tool: Any) -> _Tool:
    metadata = tool.metadata
    return _tool(metadata.name, metadata.description, metadata.get_parameters_dict())


def _openai_agents(tool: Any) -> _Tool:
    return _tool(tool.name, tool.description, tool.params_json_schema)


def _anthropic(tool: Any) -> _Tool:
    param = tool if isinstance(tool, dict) else tool.to_dict()
    return _tool(param["name"], param.get("description"), param["input_schema"])


def _crewai(tool: Any) -> _Tool:
    return _tool(tool.name, tool.description, _json_schema(tool.args_schema))


def _agent_framework(tool: Any) -> _Tool:
    return _tool(tool.name, tool.description, tool.parameters())


# (MRO package prefixes, the §10 attribute that identifies a tool, reader).
_READERS: tuple[tuple[tuple[str, ...], str, Callable[[Any], _Tool]], ...] = (
    (("langchain_core",), "tool_call_schema", _langchain),
    (("strands",), "tool_spec", _strands),
    (("google.adk",), "_get_declaration", _adk),
    (("llama_index",), "metadata", _llamaindex),
    (("agents",), "params_json_schema", _openai_agents),
    (("anthropic",), "input_schema", _anthropic),
    (("crewai",), "args_schema", _crewai),
    (("agent_framework",), "to_json_schema_spec", _agent_framework),
)

# An Anthropic tool param is a TypedDict, so a plain dict at run time.
_ANTHROPIC_PARAM_KEYS = frozenset({"name", "input_schema"})


def _reader(obj: object) -> Callable[[Any], _Tool] | None:
    if isinstance(obj, dict):
        keys = set(obj)
        if _ANTHROPIC_PARAM_KEYS <= keys <= _ANTHROPIC_PARAM_KEYS | {"description"}:
            return _anthropic
        return None
    for prefixes, attribute, reader in _READERS:
        if _from(obj, *prefixes) and hasattr(obj, attribute):
            return reader
    return None


# --- MCP servers (the official `mcp` SDK, 1.x FastMCP and 2.x MCPServer) --------


def _is_mcp_server(obj: object) -> bool:
    return (
        _from(obj, "mcp.server")
        and type(obj).__name__ in ("FastMCP", "MCPServer")
        and inspect.iscoroutinefunction(getattr(obj, "list_tools", None))
    )


def _mcp_tool(tool: Any) -> _Tool:
    # The wire Tool: `inputSchema` in mcp 1.x, `input_schema` (aliased
    # inputSchema) in 2.x. by_alias dumps both to the wire spelling.
    wire = tool.model_dump(by_alias=True, mode="json")
    return _tool(wire["name"], wire.get("description"), wire["inputSchema"])


async def _live_mcp_tools(server: Any) -> list[_Tool]:
    # Lazy: `mcp` is not a base dependency. 2.x ships `mcp.Client`, which takes
    # a server object and connects over memory; 1.x has the memory helper.
    import mcp

    client_class = getattr(mcp, "Client", None)
    if client_class is not None:
        async with client_class(server) as client:
            result = await client.list_tools()
    else:
        from mcp.shared.memory import (
            create_connected_server_and_client_session,
        )

        async with create_connected_server_and_client_session(server) as session:
            result = await session.list_tools()
    return [_mcp_tool(tool) for tool in result.tools]


def _type_name(obj: object) -> str:
    cls = type(obj)
    return f"{cls.__module__}.{cls.__qualname__}"

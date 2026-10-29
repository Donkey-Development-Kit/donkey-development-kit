"""The public API surface rule (#719).

Three parts: every public module declares a sorted ``__all__`` (ruff RUF022
checks the sorting); every ``donkey_kit`` type in a public ``Donkey`` or
``ToolsFacade`` signature is importable from ``donkey_kit``, and every one in a
``ToolSet`` signature is too or has a recorded provisional tier (#1425); and no
class name means two different types across the package's ``__all__`` lists.
Submodule paths are not API. ``test_public_api_snapshot.py`` pins the names.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import types
import typing
from collections.abc import Iterator
from typing import Any

import pytest

import donkey_kit
from donkey_kit import Donkey

# Deprecated aliases kept for one release (#719): importing them warns, and they
# re-export the renamed module's objects, so they add no new names.
_DEPRECATED_MODULES = {"donkey_kit.registry.governance"}


def _public_modules() -> Iterator[str]:
    """Every importable public ``donkey_kit`` module: no path segment starts with
    an underscore. A module whose optional extra is not installed is skipped."""
    yield "donkey_kit"
    for info in pkgutil.walk_packages(donkey_kit.__path__, "donkey_kit."):
        if any(part.startswith("_") for part in info.name.split(".")):
            continue
        if info.name in _DEPRECATED_MODULES:
            continue
        yield info.name


def _import(name: str) -> types.ModuleType | None:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        # An optional third-party extra (typer, yaml, starlette...) is absent in
        # the base-only job; a missing donkey_kit module would be a real bug.
        if exc.name and exc.name.split(".")[0] == "donkey_kit":
            raise
        return None


@pytest.mark.parametrize("name", sorted(_public_modules()))
def test_every_public_module_declares_all(name: str) -> None:
    module = _import(name)
    if module is None:
        pytest.skip(f"{name}: optional extra not installed")
    exported = getattr(module, "__all__", None)
    assert isinstance(exported, list), f"{name} has no __all__ list"
    assert len(set(exported)) == len(exported), f"{name}: duplicate names in __all__"
    for attr in exported:
        assert hasattr(module, attr), f"{name}.__all__ names missing attribute {attr!r}"


def test_no_class_name_means_two_types_across_all() -> None:
    seen: dict[str, tuple[object, str]] = {}
    clashes: list[str] = []
    for name in _public_modules():
        module = _import(name)
        if module is None:
            continue
        for attr in getattr(module, "__all__", ()):
            obj = getattr(module, attr)
            if not isinstance(obj, type):
                continue
            first = seen.setdefault(attr, (obj, name))
            if first[0] is not obj:
                clashes.append(f"{attr}: {first[1]} vs {name}")
    assert not clashes, "class names exported as two different types:\n" + "\n".join(clashes)


class _External:
    """Stand-in for a type only imported under ``TYPE_CHECKING`` (a framework
    type such as ``openai.AsyncOpenAI``): not a donkey_kit type, so not checked."""


def _type_hints(
    func: Any, module_globals: dict[str, Any], stand_ins: dict[str, Any]
) -> dict[str, Any]:
    """``typing.get_type_hints`` with every name the module does not define at
    runtime (a ``TYPE_CHECKING``-only import) resolved to :class:`_External`.

    A plain dict that learns each missing name from the ``NameError``, not a
    ``Mapping`` with a fallback ``__getitem__``: Python 3.14's
    ``ForwardRef.evaluate`` copies ``localns`` with ``dict()`` before using it
    (#1026), which keeps only the iterated keys and drops the fallback.
    """
    while True:
        try:
            return typing.get_type_hints(func, globalns=module_globals, localns=stand_ins)
        except NameError as exc:
            if exc.name is None or exc.name in stand_ins:
                raise
            stand_ins[exc.name] = _External


def _donkey_kit_types(hint: object) -> Iterator[type]:
    """Every class defined in ``donkey_kit`` that appears anywhere in ``hint``."""
    if isinstance(hint, type) and hint.__module__.split(".")[0] == "donkey_kit":
        yield hint
    for arg in typing.get_args(hint):
        if isinstance(arg, list):  # Callable[[...], R]
            for item in arg:
                yield from _donkey_kit_types(item)
        else:
            yield from _donkey_kit_types(arg)


def _public_member_hints(owner: type = Donkey) -> Iterator[tuple[str, object]]:
    module_globals = vars(importlib.import_module(owner.__module__))
    stand_ins: dict[str, Any] = {}
    for attr, member in inspect.getmembers(owner):
        if attr.startswith("_"):
            continue
        func = member.fget if isinstance(member, property) else member
        func = getattr(func, "__func__", func)
        if not callable(func):
            continue
        hints = _type_hints(func, module_globals, stand_ins)
        for hint in hints.values():
            yield attr, hint
    # Donkey's lazily resolved adapter attributes are framework-specific and come
    # from their own extras, so they are documented per adapter, not here.


def _exported_from_root(cls: type) -> bool:
    # __all__, not getattr: a deprecated alias resolves but is not an export.
    return cls.__name__ in donkey_kit.__all__ and getattr(donkey_kit, cls.__name__) is cls


# The stable classes whose public members must only take and return names from
# donkey_kit.__all__ (#719, and #1425 for the tool surface).
_ROOT_TYPED = (Donkey, donkey_kit.ToolsFacade)

# Types that a stable class's public signature uses but that are in the
# provisional tier (ADR 0006 rule 1): exported from the named public module, not
# from donkey_kit. Each one is a recorded tier decision (#1425), so a new type
# in these signatures fails until it is exported from donkey_kit or listed here.
_PROVISIONAL_SIGNATURE_TYPES = {
    "McpServerHandle": "donkey_kit.registry",
    "ToolDescriptor": "donkey_kit.tools",
    "ToolExclusion": "donkey_kit.tools",
}


@pytest.mark.parametrize("owner", _ROOT_TYPED, ids=lambda c: c.__name__)
def test_signature_types_are_importable_from_donkey_kit(owner: type) -> None:
    missing = {
        f"{owner.__name__}.{attr} -> {cls.__module__}.{cls.__qualname__}"
        for attr, hint in _public_member_hints(owner)
        for cls in _donkey_kit_types(hint)
        if not _exported_from_root(cls)
    }
    assert not missing, "not exported from donkey_kit:\n" + "\n".join(sorted(missing))


def test_toolset_signature_types_have_a_recorded_tier() -> None:
    unrecorded: set[str] = set()
    for attr, hint in _public_member_hints(donkey_kit.ToolSet):
        for cls in _donkey_kit_types(hint):
            if _exported_from_root(cls):
                continue
            home = _PROVISIONAL_SIGNATURE_TYPES.get(cls.__name__)
            module = importlib.import_module(home) if home else None
            if module is None or cls.__name__ not in module.__all__:
                unrecorded.add(f"ToolSet.{attr} -> {cls.__module__}.{cls.__qualname__}")
            elif getattr(module, cls.__name__) is not cls:
                unrecorded.add(f"ToolSet.{attr} -> {cls.__qualname__} is not {home}'s")
    assert not unrecorded, (
        "export these from donkey_kit, or record them in _PROVISIONAL_SIGNATURE_TYPES:\n"
        + "\n".join(sorted(unrecorded))
    )


def test_the_check_sees_the_types_it_must() -> None:
    """Guard the check itself: it must see the types the issue named."""
    seen = {cls for _, hint in _public_member_hints() for cls in _donkey_kit_types(hint)}
    assert {donkey_kit.LastCall, donkey_kit.RunScope, donkey_kit.ToolsFacade} <= seen
    tools = {
        cls
        for _, hint in _public_member_hints(donkey_kit.ToolsFacade)
        for cls in _donkey_kit_types(hint)
    }
    assert {donkey_kit.GovernanceCriteria, donkey_kit.McpAuth, donkey_kit.ToolSet} <= tools
    toolset = {
        cls.__name__
        for _, hint in _public_member_hints(donkey_kit.ToolSet)
        for cls in _donkey_kit_types(hint)
    }
    assert set(_PROVISIONAL_SIGNATURE_TYPES) | {"ToolSet"} <= toolset


def test_renamed_names_keep_deprecated_aliases() -> None:
    import sys

    from donkey_kit.registry import criteria

    sys.modules.pop("donkey_kit.registry.governance", None)
    with pytest.warns(DeprecationWarning, match="registry.criteria"):
        legacy = importlib.import_module("donkey_kit.registry.governance")
    assert legacy.GovernanceCriteria is criteria.GovernanceCriteria


# Types that exist only for a verification-blocked surface (registry discovery,
# governed-state checks, publication, MCP tool calls). They live in
# donkey_kit.experimental, never in the stable namespace (#730).
_BLOCKED_ONLY = {
    "STRICT",
    "AssetRef",
    "AssetType",
    "Contact",
    "Publication",
    "PublicationAssetType",
    "PublicationDrift",
    "RegistryError",
    "ToolInvocationError",
}

# Blocked-only names added to donkey_kit.experimental after #730. They were never
# in the stable namespace, so they have no deprecated top-level alias.
_NEW_IN_EXPERIMENTAL = {"tool_access_denial"}

# Names that moved up from donkey_kit.experimental once the surfaces that take
# and raise them were verified (#1425). experimental keeps a deprecated alias.
_PROMOTED_FROM_EXPERIMENTAL = {"GovernanceCriteria", "ToolNotInProfile", "UngovernedMcpServer"}

# New in 0.2.0 and kept out of donkey_kit.__all__ (#1425): provisional, from
# donkey_kit.core (ADR 0006 rule 1).
_PROVISIONAL_IN_CORE = {"PolicyRegistration", "RefusalContext", "RunUsage"}

# Names of the refused provisioning control plane, deleted in #730.
_REFUSED = {
    "Governance",
    "GovernanceDrift",
    "GatewayTarget",
    "PlatformTeamOnly",
    "ProvisioningError",
}


def test_top_level_exports_no_blocked_or_refused_type() -> None:
    leaked = (_BLOCKED_ONLY | _NEW_IN_EXPERIMENTAL | _REFUSED) & set(donkey_kit.__all__)
    assert not leaked, f"blocked/refused types in donkey_kit.__all__: {sorted(leaked)}"
    for name in _REFUSED:
        assert not hasattr(donkey_kit, name), name


@pytest.mark.parametrize("name", sorted(_BLOCKED_ONLY))
def test_moved_names_keep_a_deprecated_top_level_alias(name: str) -> None:
    # Deprecate before removing (CONTRIBUTING): the old donkey_kit.<name>
    # spelling resolves to the experimental object, with a warning naming it.
    from donkey_kit import experimental

    with pytest.warns(DeprecationWarning, match=rf"donkey_kit\.{name} .*donkey_kit\.experimental"):
        assert getattr(donkey_kit, name) is getattr(experimental, name)


def test_experimental_is_exactly_the_blocked_only_types() -> None:
    from donkey_kit import experimental

    assert set(experimental.__all__) == _BLOCKED_ONLY | _NEW_IN_EXPERIMENTAL
    for name in experimental.__all__:
        assert getattr(experimental, name) is not None


@pytest.mark.parametrize("name", sorted(_PROMOTED_FROM_EXPERIMENTAL))
def test_promoted_names_are_stable_and_keep_an_experimental_alias(name: str) -> None:
    import warnings

    from donkey_kit import experimental

    assert name in donkey_kit.__all__
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        promoted = getattr(donkey_kit, name)
    with pytest.warns(
        DeprecationWarning, match=rf"donkey_kit\.experimental\.{name} .*import it from donkey_kit"
    ):
        assert getattr(experimental, name) is promoted


def test_experimental_still_refuses_an_unknown_name() -> None:
    from donkey_kit import experimental

    with pytest.raises(AttributeError, match="no attribute 'Nope'"):
        _ = experimental.Nope


@pytest.mark.parametrize("name", sorted(_PROVISIONAL_IN_CORE))
def test_provisional_names_are_out_of_root_and_in_core(name: str) -> None:
    import donkey_kit.core

    assert name not in donkey_kit.__all__
    assert not hasattr(donkey_kit, name)
    assert name in donkey_kit.core.__all__

"""The public API surface rule (#719).

Three parts: every public module declares a sorted ``__all__`` (ruff RUF022
checks the sorting); every ``donkey_kit`` type in a public ``Donkey`` signature
is importable from ``donkey_kit``; and no class name means two different types
across the package's ``__all__`` lists. Submodule paths are not API.
"""

from __future__ import annotations

import builtins
import importlib
import inspect
import pkgutil
import types
import typing
from collections.abc import Iterator, Mapping
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


class _Namespace(Mapping[str, Any]):
    """Resolve a name from the module, then builtins, else :class:`_External`."""

    def __init__(self, module_globals: dict[str, Any]) -> None:
        self._globals = module_globals

    def __getitem__(self, key: str) -> Any:
        if key in self._globals:
            return self._globals[key]
        if hasattr(builtins, key):
            return getattr(builtins, key)
        return _External

    def __iter__(self) -> Iterator[str]:
        return iter(self._globals)

    def __len__(self) -> int:
        return len(self._globals)


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


def _public_member_hints() -> Iterator[tuple[str, object]]:
    module_globals = vars(importlib.import_module(Donkey.__module__))
    ns = _Namespace(module_globals)
    for attr, member in inspect.getmembers(Donkey):
        if attr.startswith("_"):
            continue
        func = member.fget if isinstance(member, property) else member
        func = getattr(func, "__func__", func)
        if not callable(func):
            continue
        hints = typing.get_type_hints(func, globalns=module_globals, localns=ns)
        for hint in hints.values():
            yield attr, hint
    # The lazily resolved adapter attributes are framework-specific and come
    # from their own extras, so they are documented per adapter, not here.


def test_donkey_signature_types_are_importable_from_donkey_kit() -> None:
    missing = {
        f"Donkey.{attr} -> {cls.__module__}.{cls.__qualname__}"
        for attr, hint in _public_member_hints()
        for cls in _donkey_kit_types(hint)
        # __all__, not getattr: a deprecated alias resolves but is not an export.
        if cls.__name__ not in donkey_kit.__all__ or getattr(donkey_kit, cls.__name__) is not cls
    }
    assert not missing, "not exported from donkey_kit:\n" + "\n".join(sorted(missing))


def test_the_check_sees_the_types_it_must() -> None:
    """Guard the check itself: it must see the types the issue named."""
    seen = {cls for _, hint in _public_member_hints() for cls in _donkey_kit_types(hint)}
    assert {donkey_kit.LastCall, donkey_kit.RunScope, donkey_kit.ToolsFacade} <= seen


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
    "GovernanceCriteria",
    "Publication",
    "PublicationAssetType",
    "PublicationDrift",
    "RegistryError",
    "ToolInvocationError",
}

# Names of the refused provisioning control plane, deleted in #730.
_REFUSED = {
    "Governance",
    "GovernanceDrift",
    "GatewayTarget",
    "PlatformTeamOnly",
    "ProvisioningError",
}


def test_top_level_exports_no_blocked_or_refused_type() -> None:
    leaked = (_BLOCKED_ONLY | _REFUSED) & set(donkey_kit.__all__)
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

    assert set(experimental.__all__) == _BLOCKED_ONLY
    for name in experimental.__all__:
        assert getattr(experimental, name) is not None


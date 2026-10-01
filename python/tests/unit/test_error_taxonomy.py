"""The whole DonkeyError tree honours one contract (BG §1.2, #182, #715).

Every subclass — walked recursively, so a new class cannot opt out by being
added somewhere this file forgot to list — carries a non-empty
``remediation`` and is importable from ``donkey_kit``. A handler written from
the docs, ``except DonkeyError as e: log(e.remediation)``, must never fail
inside its own ``except``.
"""

from __future__ import annotations

from typing import Any

import pytest

import donkey_kit
from donkey_kit import DonkeyError, PlatformTeamOnly
from donkey_kit.governance import GatewayTarget, Governance

# Keyword arguments a subclass cannot be built without. Every other subclass
# takes only the message.
_REQUIRED_KWARGS: dict[str, dict[str, Any]] = {
    "BudgetReserveReached": {"fraction_used": 0.9, "reserve": 0.1},
    "ModelSubstituted": {"requested_model": "a", "served_model": "b"},
}


def _sdk_subclasses(cls: type[DonkeyError]) -> list[type[DonkeyError]]:
    found: list[type[DonkeyError]] = []
    for sub in cls.__subclasses__():
        if sub.__module__.startswith("donkey_kit."):
            found.append(sub)
        found.extend(_sdk_subclasses(sub))
    return found


_TREE = [DonkeyError, *_sdk_subclasses(DonkeyError)]


def _build(cls: type[DonkeyError], **kw: Any) -> DonkeyError:
    return cls("boom", **_REQUIRED_KWARGS.get(cls.__name__, {}), **kw)


def test_the_walk_finds_the_documented_tree() -> None:
    # Guards the walk itself: if it silently returned nothing, every test below
    # would pass vacuously.
    names = {cls.__name__ for cls in _TREE}
    assert {"PolicyViolation", "PIIDetected", "UpstreamRequestError", "PlatformTeamOnly"} <= names


@pytest.mark.parametrize("cls", _TREE, ids=lambda c: c.__name__)
def test_every_error_is_exported_from_donkey_kit(cls: type[DonkeyError]) -> None:
    assert cls.__name__ in donkey_kit.__all__
    assert getattr(donkey_kit, cls.__name__) is cls


@pytest.mark.parametrize("cls", _TREE, ids=lambda c: c.__name__)
def test_every_error_ships_its_own_nonempty_default(cls: type[DonkeyError]) -> None:
    assert "remediation" in vars(cls), f"{cls.__name__} inherits its remediation"
    assert _build(cls).remediation.strip()


@pytest.mark.parametrize("cls", _TREE, ids=lambda c: c.__name__)
def test_every_error_accepts_a_remediation_override(cls: type[DonkeyError]) -> None:
    assert _build(cls, remediation="do the specific thing").remediation == (
        "do the specific thing"
    )


@pytest.mark.parametrize("cls", _TREE, ids=lambda c: c.__name__)
@pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
def test_every_error_rejects_a_blank_remediation(cls: type[DonkeyError], bad: str) -> None:
    with pytest.raises(ValueError, match="non-empty remediation"):
        _build(cls, remediation=bad)


def test_classify_is_exported_from_donkey_kit() -> None:
    from donkey_kit.core.errors import classify

    assert "classify" in donkey_kit.__all__
    assert donkey_kit.classify is classify


async def test_governance_apply_raises_a_typed_refusal() -> None:
    target = GatewayTarget(mode="local", base_url="http://localhost", connected=False)
    with pytest.raises(PlatformTeamOnly) as caught:
        await Governance(name="t", gateway=target).apply(target)
    # Still a PermissionError, so handlers written before #715 keep working.
    assert isinstance(caught.value, PermissionError)
    assert "resolve()" in caught.value.remediation

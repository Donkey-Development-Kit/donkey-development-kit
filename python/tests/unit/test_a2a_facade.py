"""Base-only outbound surface and lazy-extra contract (#1395)."""

from __future__ import annotations

import builtins
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from donkey_kit import A2AFacade, Donkey, DonkeyConfig


async def test_surface_does_not_need_sdk() -> None:
    async with Donkey(DonkeyConfig(telemetry=False)) as donkey:
        assert isinstance(donkey.a2a, A2AFacade)
        assert donkey.a2a is donkey.a2a
        assert list(inspect.signature(donkey.a2a.send).parameters) == [
            "ref",
            "message",
            "auth",
            "criteria",
        ]
        assert list(inspect.signature(donkey.a2a.stream).parameters) == [
            "ref",
            "message",
            "auth",
            "criteria",
        ]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("allow", [False, True])
async def test_missing_extra_is_curated_before_registry(
    monkeypatch: pytest.MonkeyPatch, streaming: bool, allow: bool
) -> None:
    original = builtins.__import__

    def without_sdk(name, *args, **kwargs):
        if name == "a2a" or name.startswith("a2a."):
            raise ModuleNotFoundError("no a2a")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_sdk)
    async with Donkey(DonkeyConfig(telemetry=False, allow_a2a_sdk_telemetry=allow)) as donkey:
        explain = AsyncMock(side_effect=AssertionError("must not reach registry"))
        monkeypatch.setattr(donkey.registry, "explain", explain)
        with pytest.raises(ImportError, match=r'pip install "donkey-kit\[a2a\]"'):
            if streaming:
                async with donkey.a2a.stream("g/a/1", object()):
                    pytest.fail("missing extra allowed stream")
            else:
                await donkey.a2a.send("g/a/1", object())
        explain.assert_not_awaited()


@pytest.mark.parametrize(
    "state",
    [
        {},
        {"otel_enabled": False},
        {"otel_installed": "false"},
        {"otel_installed": True, "otel_enabled": 0},
    ],
)
def test_unknown_sdk_tracing_state_fails_closed(monkeypatch, state) -> None:
    from donkey_kit.a2a.client import _check_sdk_telemetry
    from donkey_kit.core.errors import ConfigError

    original = builtins.__import__

    def unknown_sdk(name, *args, **kwargs):
        if name == "a2a.utils":
            return SimpleNamespace(telemetry=SimpleNamespace(**state))
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unknown_sdk)
    with pytest.raises(ConfigError, match="cannot be determined"):
        _check_sdk_telemetry()


def test_missing_sdk_telemetry_module_fails_closed(monkeypatch) -> None:
    from donkey_kit.a2a.client import _check_sdk_telemetry
    from donkey_kit.core.errors import ConfigError

    original = builtins.__import__

    def unknown_sdk(name, *args, **kwargs):
        if name == "a2a.utils":
            raise ImportError("telemetry interface changed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unknown_sdk)
    with pytest.raises(ConfigError, match="cannot be determined"):
        _check_sdk_telemetry()

"""Sandbox-suite fixtures — LIVE calls against the PROVISIONED LLM Gateway proxies (#400).

Every test here is ``@pytest.mark.sandbox``, so it is deselected by default
(``addopts`` in ``pyproject.toml``) and runs only under ``pytest -m sandbox``
with ``DONKEY_SANDBOX_TESTS=1``. Unlike the conformance kit's *never-skip* rule
(``tests/conformance/suite.py``), a skip here is **correct**: this marker gates
*infra availability* (a real proxy plus its consumer creds), not framework
support.

Targets are declared in ``tests/sandbox/proxies.toml`` (gitignored; copy from
``proxies.toml.example``). Each ``[[proxy]]`` names a ``base_url``, the routed
``model``, and the two env vars holding that proxy's consumer ``client_id`` /
``client_secret`` — minted per proxy via the ``ddk-request-llm-proxy-access``
skill and kept in the SDK repo's gitignored ``.env``. The manifest itself holds
**no secrets**: only base URLs and env-var *names*.

This is the multi-target design decision from #400: ``core/config.DonkeyConfig``
resolves a single ``DONKEY_LLM_PROXY_*`` triple, but the provisioning repo hosts
several proxies, each with its own credential pair. The manifest is how the
suite addresses them all without contorting the single-triple env config — so
``core/`` is untouched.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 has no stdlib tomllib; the [core] dep ``tomli`` backfills it.
    import tomli as tomllib

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.donkey import Donkey

MANIFEST = Path(__file__).resolve().parent / "proxies.toml"


@dataclass(frozen=True)
class ProxyTarget:
    """One provisioned proxy with its consumer creds already resolved."""

    key: str
    base_url: str
    model: str
    client_id: str
    client_secret: str


def _load_targets() -> dict[str, ProxyTarget]:
    """Parse the gitignored manifest, resolving each proxy's creds from the env
    vars it names. A proxy whose creds are absent is dropped (not an error) — the
    per-proxy accessor skips cleanly when its target is missing."""
    if not MANIFEST.exists():
        return {}
    data = tomllib.loads(MANIFEST.read_text())
    targets: dict[str, ProxyTarget] = {}
    for entry in data.get("proxy", []):
        client_id = os.environ.get(entry["client_id_env"], "")
        client_secret = os.environ.get(entry["client_secret_env"], "")
        if not (client_id and client_secret):
            continue
        targets[entry["key"]] = ProxyTarget(
            key=entry["key"],
            base_url=entry["base_url"],
            model=entry["model"],
            client_id=client_id,
            client_secret=client_secret,
        )
    return targets


def _donkey_for(target: ProxyTarget) -> Donkey:
    """A ``Donkey`` pointed at one provisioned proxy, built from an explicit
    config rather than the environment so each proxy carries its own credential
    pair (#400)."""
    cfg = DonkeyConfig(
        llm_proxy_url=target.base_url,
        llm_proxy_client_id=target.client_id,
        llm_proxy_client_secret=target.client_secret,
    )
    return Donkey(cfg)


@pytest.fixture(scope="session")
def sandbox_targets() -> dict[str, ProxyTarget]:
    """All provisioned proxies whose creds are present. Skips the whole suite
    when sandbox tests are not opted into, or when nothing is configured."""
    if os.environ.get("DONKEY_SANDBOX_TESTS") != "1":
        pytest.skip("sandbox tests are opt-in: set DONKEY_SANDBOX_TESTS=1")
    targets = _load_targets()
    if not targets:
        pytest.skip(
            f"no sandbox proxies configured: copy {MANIFEST.name} from "
            "proxies.toml.example and set each proxy's *_CLIENT_ID / *_CLIENT_SECRET env vars"
        )
    return targets


@pytest.fixture
def open_proxy(
    sandbox_targets: dict[str, ProxyTarget],
) -> Callable[[str], Donkey]:
    """Return an accessor that builds a ``Donkey`` for a named proxy, skipping
    cleanly when that specific proxy is not in the manifest::

        donkey = open_proxy("openai-model-routing")
    """

    def _open(key: str) -> Donkey:
        if key not in sandbox_targets:
            pytest.skip(f"proxy {key!r} not configured in {MANIFEST.name}")
        return _donkey_for(sandbox_targets[key])

    return _open


@pytest.fixture
def model_for(
    sandbox_targets: dict[str, ProxyTarget],
) -> Callable[[str], str]:
    """Return the routed ``model`` id declared for a named proxy, so tests do not
    hardcode a provider/model the manifest owns."""

    def _model(key: str) -> str:
        if key not in sandbox_targets:
            pytest.skip(f"proxy {key!r} not configured in {MANIFEST.name}")
        return sandbox_targets[key].model

    return _model

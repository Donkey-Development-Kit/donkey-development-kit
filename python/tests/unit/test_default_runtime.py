"""The module-level adapter factories run on the process-default runtime (#725).

``donkey_kit.integrations.<fw>.<factory>()`` is documented as equivalent to
``Donkey.from_env().<fw>.<factory>()``. These tests hold it to that: every
default adapter shares one client from :func:`donkey_kit.core.runtime.default`,
with the same budget, auth and OTLP wiring a ``Donkey`` gets, the same request
headers and the same ``connection_kwargs()``; and the runtime is closed at exit.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import textwrap
import threading
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core import runtime
from donkey_kit.core.auth import AnypointConnectedApp
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.transport import (
    DonkeyAsyncClient,
    DonkeyAsyncClientView,
    DonkeyClient,
    DonkeyClientView,
)
from donkey_kit.integrations import ADAPTERS
from donkey_kit.integrations._base import Adapter, default_adapter
from donkey_kit.llm.client import LLMClient

_SRC = Path(__file__).resolve().parents[2] / "src" / "donkey_kit"


def _adapter_cls(name: str) -> type[Adapter]:
    spec = ADAPTERS[name]
    module = importlib.import_module(spec.module, package="donkey_kit.integrations")
    cls: type[Adapter] = getattr(module, spec.cls)
    return cls


def _on_donkey(donkey: Donkey, name: str) -> Adapter:
    # Exactly what ``Donkey.__getattr__`` builds, without its install probe, so
    # the comparison runs on a base install with no framework present.
    return _adapter_cls(name)(donkey._cfg, donkey._http, donkey._sync_http_client)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    monkeypatch.chdir(tmp_path)  # no stray .donkey-kit.toml
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://proxy")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "app-id")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "app-secret")
    return monkeypatch


def test_every_default_adapter_shares_the_default_runtime(env: pytest.MonkeyPatch) -> None:
    rt = runtime.default()
    adapters = [default_adapter(_adapter_cls(name)) for name in ADAPTERS]
    assert all(a._http is rt.http for a in adapters)
    assert all(a._sync_http() is rt.sync_http() for a in adapters)


@pytest.mark.parametrize("owner", ["adapter", "llm"])
def test_standalone_owner_closes_only_its_blocking_client(owner: str) -> None:
    cfg = DonkeyConfig(llm_proxy_url="https://proxy")
    shared = DonkeyAsyncClient(cfg, None)
    instance = (
        _adapter_cls("langgraph")(cfg, shared) if owner == "adapter" else LLMClient(cfg, shared)
    )
    blocking = instance._sync_http()

    assert not blocking.is_closed
    instance.close()
    instance.close()
    assert blocking.is_closed
    assert not shared.is_closed


@pytest.mark.parametrize("owner", ["adapter", "llm"])
def test_closing_standalone_owner_without_a_blocking_client_is_safe(owner: str) -> None:
    cfg = DonkeyConfig(llm_proxy_url="https://proxy")
    shared = DonkeyAsyncClient(cfg, None)
    instance = (
        _adapter_cls("langgraph")(cfg, shared) if owner == "adapter" else LLMClient(cfg, shared)
    )

    instance.close()
    assert instance._owned_sync is None
    assert not shared.is_closed


@pytest.mark.parametrize("owner", ["adapter", "llm"])
def test_runtime_injected_blocking_client_stays_caller_owned(owner: str) -> None:
    cfg = DonkeyConfig(llm_proxy_url="https://proxy")
    shared = DonkeyAsyncClient(cfg, None)
    blocking = DonkeyClient(cfg)
    instance = (
        _adapter_cls("langgraph")(cfg, shared, lambda: blocking)
        if owner == "adapter"
        else LLMClient(cfg, shared, lambda: blocking)
    )

    instance._sync_http()
    instance.close()
    assert not blocking.is_closed
    blocking.close()


@pytest.mark.parametrize("owner", ["adapter", "llm"])
async def test_standalone_async_close_closes_owned_blocking_client(owner: str) -> None:
    cfg = DonkeyConfig(llm_proxy_url="https://proxy")
    shared = DonkeyAsyncClient(cfg, None)
    instance = (
        _adapter_cls("langgraph")(cfg, shared) if owner == "adapter" else LLMClient(cfg, shared)
    )
    blocking = instance._sync_http()

    await instance.aclose()
    assert blocking.is_closed
    assert not shared.is_closed


@pytest.mark.parametrize("name", list(ADAPTERS))
def test_default_adapter_is_wired_like_donkey_from_env(name: str, env: pytest.MonkeyPatch) -> None:
    if name == "openai_agents":
        # Its only governed value is a pre-built AsyncOpenAI (no base-only install).
        pytest.importorskip("openai")
    rt = runtime.default()
    adapter = default_adapter(_adapter_cls(name))
    donkey = Donkey.from_env()

    # Budget: the shared clients feed the runtime's one Budget, as on a Donkey.
    assert adapter._http._budget is rt.budget
    assert donkey._http._budget is donkey.budget
    assert adapter._sync_http()._budget is rt.budget
    # Auth: the connected-app provider is built on the control plane, and the
    # data plane carries no token provider in client-id mode — on both forms.
    assert isinstance(rt.auth, AnypointConnectedApp)
    assert type(rt.auth) is type(donkey._auth)
    assert rt.control_http.token_provider is rt.auth
    assert donkey._control_http.token_provider is donkey._auth
    assert adapter._http.token_provider is donkey._http.token_provider is None

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # one-time unverified-value warnings
        assert _normalised(adapter.connection_kwargs()) == _normalised(
            _on_donkey(donkey, name).connection_kwargs()
        )


async def test_default_runtime_sends_the_same_headers_as_donkey(
    env: pytest.MonkeyPatch,
) -> None:
    rt = runtime.default()
    donkey = Donkey.from_env()
    seen: list[httpx.Headers] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(200, json={})

    for client in (rt.http, donkey._http):
        client.governed_transport.replace_inner(httpx.MockTransport(capture))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        async with donkey.run(id="run-1"):
            await rt.http.post("https://proxy/responses", json={"model": "gpt-4o"})
            await donkey._http.post("https://proxy/responses", json={"model": "gpt-4o"})
    await donkey.aclose()

    call_id = rt.http._call_id_header.lower()  # unique per call by design
    factory, explicit = ({k: v for k, v in h.items() if k != call_id} for h in seen)
    assert factory == explicit


def _normalised(value: Any) -> Any:
    """``connection_kwargs()`` with each client (or view of one, #733) replaced
    by what identifies its governance, since the two forms hold distinct (but
    identically built) client objects. A bridged ``httpx2`` client (anthropic>=1,
    #701; openai>=3, async or sync, #728) is identified by the shared client its
    transport forwards to (#903)."""
    if isinstance(value, Mapping):
        return {k: _normalised(v) for k, v in value.items()}
    if isinstance(value, (DonkeyAsyncClientView, DonkeyClientView)):
        return (type(value).__name__, _normalised(value._shared))
    if isinstance(value, (DonkeyAsyncClient, DonkeyClient)):
        return (type(value).__name__, value._cfg, getattr(value, "_control_plane", False))
    if type(value).__name__ == "AsyncOpenAI":
        return (
            "AsyncOpenAI",
            str(value.base_url),
            {k: v for k, v in value.default_headers.items() if isinstance(v, str)},
            value.max_retries,
            _normalised(value._client),
        )
    transport = getattr(value, "_transport", None)
    if type(transport).__name__ in ("DonkeyForwardingTransport", "DonkeyForwardingSyncTransport"):
        return (type(value).__name__, _normalised(transport._client))
    return value


def test_default_runtime_bootstraps_otlp_export(
    env: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    env.setattr(runtime, "configure_otlp_export", calls.append)
    rt = runtime.default()
    assert calls == [rt.config]


def test_default_is_one_instance_under_concurrent_first_use(
    env: pytest.MonkeyPatch,
) -> None:
    barrier = threading.Barrier(8)
    seen: list[runtime.Runtime] = []

    def first_use() -> None:
        barrier.wait()
        seen.append(runtime.default())

    threads = [threading.Thread(target=first_use) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(rt) for rt in seen}) == 1


def test_close_default_closes_every_transport_and_resets(env: pytest.MonkeyPatch) -> None:
    rt = runtime.default()
    owned_auth = rt.owned_auth_http
    sync = rt.sync_http()
    stale = default_adapter(_adapter_cls("langgraph"))

    runtime.close_default()

    assert rt.http.is_closed and rt.control_http.is_closed and sync.is_closed
    assert owned_auth is not None and owned_auth.is_closed
    fresh = runtime.default()
    assert fresh is not rt
    # A cached default adapter follows the replacement instead of the closed client.
    assert default_adapter(_adapter_cls("langgraph")) is not stale
    assert default_adapter(_adapter_cls("langgraph"))._http is fresh.http


def test_default_runtime_is_closed_at_interpreter_exit(tmp_path: Path) -> None:
    # atexit runs last-registered first, so a hook registered before the default
    # runtime exists runs after its close hook and can observe the result.
    script = textwrap.dedent(
        """
        import atexit
        from donkey_kit.core import runtime

        held = {}

        @atexit.register
        def report():
            rt = held["rt"]
            print("closed", rt.http.is_closed, rt.control_http.is_closed)

        held["rt"] = runtime.default()
        """
    )
    env = {"DONKEY_LLM_PROXY_URL": "https://proxy", "PATH": ""}
    out = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        check=True,
    )
    assert out.stdout.strip() == "closed True True"


def test_no_http_client_is_built_outside_the_runtime() -> None:
    # The runtime is the one place shared async clients are built (#725); the
    # factory itself is defined in core/transport/async_client.py.
    allowed = {"core/runtime.py", "core/transport/async_client.py"}
    offenders = [
        str(path.relative_to(_SRC))
        for path in sorted(_SRC.rglob("*.py"))
        if "build_http_client(" in path.read_text()
        and path.relative_to(_SRC).as_posix() not in allowed
    ]
    assert offenders == []

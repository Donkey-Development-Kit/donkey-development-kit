"""Donkey public surface: lazy adapters, curated ImportError, base-package
import safety (working instruction #9)."""

from __future__ import annotations

import pytest

from donkey_kit import Donkey, DonkeyConfig


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )


def test_uninstalled_adapter_raises_curated_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force "framework not installed" regardless of what happens to be present in
    # the dev env, so the assertion is deterministic: access must raise
    # ImportError with the exact install command, never a bare
    # ModuleNotFoundError (BG §1.8).
    monkeypatch.setattr("donkey_kit.donkey._framework_installed", lambda _probe: False)
    fab = Donkey(_cfg())
    with pytest.raises(ImportError) as exc:
        _ = fab.langgraph
    assert 'donkey-kit[langgraph]' in str(exc.value)


def test_unknown_attribute_raises_attribute_error() -> None:
    fab = Donkey(_cfg())
    with pytest.raises(AttributeError):
        _ = fab.not_a_framework


def test_openai_agents_adapter_import_error_names_the_new_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Agents SDK adapter lives at ``donkey.openai_agents`` and its curated
    ImportError points at ``donkey-kit[openai-agents]`` (#277)."""
    monkeypatch.setattr("donkey_kit.donkey._framework_installed", lambda _probe: False)
    fab = Donkey(_cfg())
    with pytest.raises(ImportError) as exc:
        _ = fab.openai_agents
    assert 'donkey-kit[openai-agents]' in str(exc.value)


def test_openai_is_a_real_method_not_the_agents_adapter() -> None:
    """``donkey.openai()`` is the raw governed client (`BG §1.1`), returning a
    native ``openai.AsyncOpenAI`` (or ``OpenAI`` with ``sync=True``) — not the
    Agents SDK adapter (#277)."""
    openai = pytest.importorskip("openai")

    with Donkey(_cfg()) as fab:
        assert isinstance(fab.openai(), openai.AsyncOpenAI)
        assert isinstance(fab.openai(sync=True), openai.OpenAI)


def test_openai_never_probes_the_agents_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Because ``openai()`` is a real method it shadows ``__getattr__``: with the
    Agents SDK 'not installed', it still returns a client rather than raising the
    curated ImportError for ``donkey-kit[openai-agents]`` (#277)."""
    openai = pytest.importorskip("openai")
    monkeypatch.setattr("donkey_kit.donkey._framework_installed", lambda _probe: False)

    with Donkey(_cfg()) as fab:
        assert isinstance(fab.openai(), openai.AsyncOpenAI)


def test_run_context_binds_correlation_id() -> None:
    from donkey_kit.core.telemetry import current_correlation_id

    fab = Donkey(_cfg())
    with fab.run_context("abc123") as rid:
        assert rid == "abc123"
        assert current_correlation_id() == "abc123"
    assert current_correlation_id() is None


def test_run_binds_correlation_id_sync() -> None:
    """``donkey.run(id=…)`` is the headline API (#195); a plain ``with`` binds the
    run id and restores it on exit."""
    from donkey_kit.core.telemetry import current_correlation_id

    fab = Donkey(_cfg())
    with fab.run(id="ticket-7") as rid:
        assert rid == "ticket-7"
        assert current_correlation_id() == "ticket-7"
    assert current_correlation_id() is None


async def test_run_binds_correlation_id_async() -> None:
    """The same object works under ``async with`` — the headline shape
    ``async with donkey.run(id=ticket.id): await agent.run(...)`` (#195)."""
    from donkey_kit.core.telemetry import current_correlation_id

    fab = Donkey(_cfg())
    async with fab.run(id="ticket-async") as rid:
        assert rid == "ticket-async"
        assert current_correlation_id() == "ticket-async"
    assert current_correlation_id() is None


def test_nested_runs_rebind_then_restore() -> None:
    from donkey_kit.core.telemetry import current_correlation_id

    fab = Donkey(_cfg())
    with fab.run(id="outer"):
        assert current_correlation_id() == "outer"
        with fab.run(id="inner"):
            assert current_correlation_id() == "inner"
        assert current_correlation_id() == "outer"
    assert current_correlation_id() is None


def test_run_without_id_generates_a_run_of_one() -> None:
    from donkey_kit.core.telemetry import current_correlation_id

    fab = Donkey(_cfg())
    with fab.run() as rid:
        assert rid and current_correlation_id() == rid
    assert current_correlation_id() is None


# --- cost-attribution tags on the public surface (docs/verified-apis.md §3, BG §1.7, #196) --------


def test_from_env_sets_config_level_cost_tags(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donkey_kit.core.cost import CostTags

    # Isolate from any stray .donkey-kit.toml / DONKEY_COST_* in the environment.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    for var in (
        "DONKEY_COST_TEAM",
        "DONKEY_COST_PROJECT",
        "DONKEY_COST_ENV",
        "DONKEY_COST_ENDUSER_ID",
    ):
        monkeypatch.delenv(var, raising=False)

    fab = Donkey.from_env(team="support", project="triage-v2", env="prod")
    assert fab.config.cost == CostTags(team="support", project="triage-v2", env="prod")


def test_from_env_kwargs_merge_over_env_tags(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donkey_kit.core.cost import CostTags

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("DONKEY_COST_TEAM", "env-team")
    monkeypatch.setenv("DONKEY_COST_ENV", "prod")
    # kwarg overrides team; env-supplied env dimension is preserved.
    fab = Donkey.from_env(team="kwarg-team")
    assert fab.config.cost == CostTags(team="kwarg-team", env="prod")


# --- on_model_substitution override on the public surface (BG §1.1, #309) --------


def test_from_env_sets_on_model_substitution(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The kwarg opts a Donkey into model determinism without an env var / toml —
    # it merges over the resolved config the same way the cost tags do.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("DONKEY_ON_MODEL_SUBSTITUTION", raising=False)

    assert Donkey.from_env().config.on_model_substitution == "off"  # resolved default
    fab = Donkey.from_env(on_model_substitution="raise")
    assert fab.config.on_model_substitution == "raise"


def test_model_substituted_is_a_public_export() -> None:
    # The typed error the "raise" mode surfaces must be importable from the
    # top-level package so a caller can `except ModelSubstituted` (BG §1.1, #309).
    from donkey_kit import ModelSubstituted
    from donkey_kit.core.errors import DonkeyError

    assert issubclass(ModelSubstituted, DonkeyError)


def test_run_binds_cost_override_for_the_block() -> None:
    from donkey_kit.core.cost import CostTags
    from donkey_kit.core.telemetry import current_cost_tags

    fab = Donkey(_cfg())
    assert current_cost_tags() is None
    with fab.run(team="triage", project="triage-v2"):
        assert current_cost_tags() == CostTags(team="triage", project="triage-v2")
    assert current_cost_tags() is None  # restored on exit


def test_run_without_cost_binds_nothing_on_the_cost_var() -> None:
    from donkey_kit.core.telemetry import current_cost_tags

    fab = Donkey(_cfg())
    with fab.run(id="ticket-1"):
        # A plain run binds only the correlation id — no cost override leaks in.
        assert current_cost_tags() is None


def test_llm_client_requires_proxy_config() -> None:
    from donkey_kit.core.errors import ConfigError

    fab = Donkey(DonkeyConfig())  # no proxy creds
    with pytest.raises((ConfigError, ImportError)):
        # ConfigError if openai missing check passes; either way it must not
        # silently build a client without proxy config.
        fab.llm.client()


def test_sync_llm_client_requires_proxy_config() -> None:
    """sync=True must not be a way around the config gate."""
    from donkey_kit.core.errors import ConfigError

    fab = Donkey(DonkeyConfig())
    with pytest.raises((ConfigError, ImportError)):
        fab.llm.client(sync=True)


def test_client_returns_async_by_default_and_blocking_on_request() -> None:
    openai = pytest.importorskip("openai")

    with Donkey(_cfg()) as fab:
        assert isinstance(fab.llm.client(), openai.AsyncOpenAI)
        assert isinstance(fab.llm.client(sync=True), openai.OpenAI)


def test_both_clients_carry_the_same_governed_configuration() -> None:
    """The blocking client is a transport swap, not a different contract: same
    base URL and same verified client_id/client_secret headers (docs/verified-apis.md §2/§3)."""
    pytest.importorskip("openai")

    with Donkey(_cfg()) as fab:
        blocking = fab.llm.client(sync=True)
        asynchronous = fab.llm.client()

    for built in (blocking, asynchronous):
        assert str(built.base_url) == "https://proxy"
        assert built.default_headers["client_id"] == "cid"
        assert built.default_headers["client_secret"] == "csecret"
        assert built.max_retries == 0  # retries belong to the transport (BG §1.1)


def test_blocking_transport_is_lazy_shared_and_closed_by_the_context_manager() -> None:
    pytest.importorskip("openai")

    fab = Donkey(_cfg())
    assert fab._sync_http is None  # not built until asked for
    with fab:
        fab.llm.client(sync=True)
        transport = fab._sync_http
        assert transport is not None
        fab.llm.client(sync=True)
        assert fab._sync_http is transport  # reused, not rebuilt
    assert transport.is_closed


async def test_aclose_also_closes_a_blocking_transport() -> None:
    """A caller can mix both surfaces; aclose() must not leak the sync pool."""
    pytest.importorskip("openai")

    fab = Donkey(_cfg())
    fab.llm.client(sync=True)
    transport = fab._sync_http
    assert transport is not None
    await fab.aclose()
    assert transport.is_closed

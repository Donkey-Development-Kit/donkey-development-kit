"""Configuration.

Resolution order (:meth:`DonkeyConfig.resolve`, §2.1, #727), highest first and
per key: a value set in code (a ``resolve()`` keyword argument, or one changed
afterwards with ``with_overrides`` or ``dataclasses.replace``) → env var →
``.donkey-kit.local.toml`` → ``.donkey-kit.toml`` → the user file
``$XDG_CONFIG_HOME/.donkey-kit.toml`` (``~/.config/.donkey-kit.toml`` when
``XDG_CONFIG_HOME`` is unset or empty) → default. The three files merge key by
key (nested tables such as ``[donkey.cost]`` recursively; scalars and arrays
replace), so a lower file fills what a higher one leaves unset. ``resolve(path=...)``
reads the named file in place of the working directory's ``.donkey-kit.toml``.
``from_env()`` is ``resolve()`` with no arguments. A ``DonkeyConfig(...)``
built directly reads neither env nor files. We never read ``.env`` implicitly —
the user calls ``load_dotenv()`` themselves. One declarative table,
``_FIELDS``, names each field's env var, TOML key, parser and value check.

Every resolved field records its source (a value changed in code afterwards
counts as set in code), and :meth:`DonkeyConfig.check_endpoints`
uses it so an endpoint read from the working directory's files only receives
credentials read from those same files (``DONKEY_TRUST_PROJECT_CONFIG=1`` opts
out). Endpoints must be ``https://``, except loopback hosts, or any host when
``DONKEY_ALLOW_HTTP=1`` is set in the environment.

``validated()`` reports ALL missing fields in one error, not one per run — the
one-missing-variable-per-run loop is the most common first-five-minutes
abandonment (config resolution).
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import os
import secrets
import sys
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Literal, TypedDict, cast

__all__ = [
    "LOCAL_TOML_NAME",
    "TOKEN_AUTH_MODES",
    "TOML_NAME",
    "TRUST_PROJECT_CONFIG_ENV",
    "Capability",
    "ConfigOverrides",
    "ConfigSource",
    "DonkeyConfig",
    "LlmProxyAuth",
    "OnModelSubstitution",
    "Region",
    "SourceKind",
    "missing_llm_auth_error",
]

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 has no stdlib tomllib; the [core] dep ``tomli`` backfills it.
    import tomli as tomllib

from . import _verify
from ._verify import REGION_HOSTS, UNVERIFIED_REGION_HOSTS
from .cost import CostTags
from .endpoints import STANDARD_CONTROL_PLANE_HOSTS, host_of, require_secure_url
from .errors import ConfigError, ConfigWarning
from .header_names import header_name_problem

# Imported at runtime, not only for type checking, so ``typing.get_type_hints``
# resolves the ``**overrides`` of resolve() and with_overrides() (#727).
if sys.version_info >= (3, 11):
    from typing import Unpack
else:
    from typing_extensions import Unpack

Region = Literal["us", "eu", "ca", "jp"]

# What to do when the gateway serves a different model than the one requested —
# a routing fallback substituted the model (docs/verified-apis.md §3, #309).
# ``"off"`` (default): the substitution is surfaced passively on
# ``donkey.last_call.substituted`` and the
# span, but the call succeeds. ``"raise"``: the transport raises
# :class:`~donkey_kit.core.errors.ModelSubstituted`, for callers who need model
# determinism (e.g. an evaluation whose results are only comparable per-model).
OnModelSubstitution = Literal["off", "raise"]

# How the caller authenticates to the LLM proxy DATA plane (BG §1.1, #509).
# ``"client-id"`` (default): the ``client_id``/``client_secret``
# request-header pair (client-id-enforcement, docs/verified-apis.md §2/§3). ``"jwt"``:
# a wallet-backed proxy where Client ID Enforcement is disabled and the caller is
# identified from an IdP-issued JWT validated by the JWT Validation policy, with
# NO ``client_secret`` — the parallel ingress captured live under #372
# (docs/verified-apis.md §2/§3). ``"bearer"``: a proxy that authenticates model
# calls on a bearer token alone — ``Authorization: Bearer <token>``, with no
# wallet selector and no ``client_id``/``client_secret`` pair (#836). The mode is
# durable config even though the token itself is not: in both token modes the
# rotating credential enters through an ``AuthProvider`` (see
# ``Donkey(llm_auth=...)``), never a config field. Inferring the mode from "no
# client_id set" is deliberately NOT done — it would turn a typo into a silent
# mode switch and make the per-mode missing-field report misleading (#509).
LlmProxyAuth = Literal["client-id", "jwt", "bearer"]

#: The auth modes whose data-plane credential is a token from the
#: ``Donkey(llm_auth=...)`` provider, added per send by the shared async client.
TOKEN_AUTH_MODES: frozenset[LlmProxyAuth] = frozenset({"jwt", "bearer"})

# The capability :meth:`DonkeyConfig.validated` checks the config for: the
# Anypoint control plane (registry) or the LLM proxy (BG §1.1).
Capability = Literal["control_plane", "llm"]


class ConfigOverrides(TypedDict, total=False):
    """The public :class:`DonkeyConfig` fields, each optional, as keyword
    arguments: what :meth:`DonkeyConfig.resolve`, :meth:`DonkeyConfig.with_overrides`
    and ``Donkey.from_env`` accept, so a misspelt field fails type checking
    (#716, #727). Must list exactly the dataclass's public fields; a unit test
    pins that."""

    client_id: str | None
    client_secret: str | None
    org_id: str | None
    environment: str
    region: Region
    base_url: str | None
    llm_proxy_url: str | None
    llm_proxy_client_id: str | None
    llm_proxy_client_secret: str | None
    llm_proxy_key: str | None
    llm_proxy_auth: LlmProxyAuth
    llm_proxy_wallet_client_id: str | None
    application_name: str | None
    business_group: str | None
    correlation_header: str | None
    call_id_header: str | None
    cost: CostTags
    cost_team_header: str | None
    cost_project_header: str | None
    cost_env_header: str | None
    cost_enduser_header: str | None
    timeout_s: float
    max_retries: int
    registry_cache_ttl_s: int
    telemetry: bool
    telemetry_capture_content: bool
    telemetry_install_global: bool
    on_model_substitution: OnModelSubstitution
    send_cost_headers: bool
    retry_model_calls_on_gateway_errors: bool


TOML_NAME = ".donkey-kit.toml"
LOCAL_TOML_NAME = ".donkey-kit.local.toml"
TRUST_PROJECT_CONFIG_ENV = "DONKEY_TRUST_PROJECT_CONFIG"

SourceKind = Literal["explicit", "env", "project", "local", "user", "default"]

_SOURCE_LABELS: dict[SourceKind, str] = {
    "explicit": "code",
    "env": "env",
    "project": "project file",
    "local": "local overlay",
    "user": "user file",
    "default": "default",
}

# The layers DonkeyConfig.resolve() reads, highest first (§2.1, #727): code
# (resolve() keyword arguments, or a value changed afterwards), environment
# variables, the working directory's .local overlay, its project file, the user
# file, then the field default. The three files merge key by key. The docs'
# Precedence section is tested against this order.
_PRECEDENCE: tuple[SourceKind, ...] = ("explicit", "env", "local", "project", "user", "default")

# The working-directory files. An endpoint read from one of these only receives
# credentials read from one of these (see DonkeyConfig.check_endpoints).
_WORKDIR_KINDS: frozenset[SourceKind] = frozenset({"project", "local"})
_FILE_KINDS: frozenset[SourceKind] = _WORKDIR_KINDS | {"user"}

# The keys that name a request header, each with its default name.
_HEADER_KEYS: tuple[tuple[str, str], ...] = (
    ("correlation_header", _verify.CORRELATION_ID_HEADER),
    ("call_id_header", _verify.CALL_ID_HEADER),
    ("cost_team_header", _verify.COST_TEAM_HEADER),
    ("cost_project_header", _verify.COST_PROJECT_HEADER),
    ("cost_env_header", _verify.COST_ENV_HEADER),
    ("cost_enduser_header", _verify.COST_ENDUSER_HEADER),
)

# Keys that should never sit in the committed project file.
_SECRET_KEYS = ("client_secret", "llm_proxy_client_secret", "llm_proxy_key")

_TRUE_TOKENS = ("1", "true", "yes", "on")
_FALSE_TOKENS = ("0", "false", "no", "off")


@dataclass(frozen=True)
class ConfigSource:
    """Where one resolved config field came from. ``path`` is set for file sources."""

    kind: SourceKind
    path: Path | None = None

    def __str__(self) -> str:
        return _SOURCE_LABELS[self.kind]


_EXPLICIT = ConfigSource("explicit")


# Provenance holds a keyed digest of each loaded value, never the value. The key
# is random per process, so a digest can be checked here and says nothing
# about the value anywhere else.
_PROCESS_KEY = secrets.token_bytes(32)

# When provenance can't be checked (it was recorded under another process's
# key), an endpoint keeps its recorded label and a credential counts as set in
# code: the binding rule then refuses at least what it refused at load time.
_ENDPOINT_KEYS = frozenset({"base_url", "llm_proxy_url"})


def _key_id() -> str:
    return hmac.new(_PROCESS_KEY, b"key id", hashlib.sha256).hexdigest()[:16]


def _digest(value: object) -> str:
    return hmac.new(_PROCESS_KEY, repr(value).encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class _Loaded:
    """A field's source plus a keyed digest of the value it resolved to there."""

    source: ConfigSource
    digest: str
    key_id: str


def _as_loaded(name: str, entry: object) -> _Loaded:
    """``entry`` as a :class:`_Loaded`, also accepting the plain mapping
    ``dataclasses.asdict`` turns one into. Anything else is refused."""
    if isinstance(entry, _Loaded):
        return entry
    if isinstance(entry, Mapping):
        source, digest, key_id = entry.get("source"), entry.get("digest"), entry.get("key_id")
        if isinstance(source, Mapping) and source.get("kind") in _SOURCE_LABELS:
            path = source.get("path")
            source = ConfigSource(source["kind"], None if path is None else Path(path))
        if (
            isinstance(source, ConfigSource)
            and (source.path is not None or source.kind not in _FILE_KINDS)
            and isinstance(digest, str)
            and isinstance(key_id, str)
        ):
            return _Loaded(source, digest, key_id)
    raise ConfigError(
        f"The recorded source of {name!r} is unreadable. Build the config with "
        "DonkeyConfig.from_env(), or in code without _sources."
    )


@dataclass(frozen=True)
class DonkeyConfig:
    """Everything the SDK needs to reach Agent Fabric, resolved once and immutable.

    Build it with :meth:`resolve` (or :meth:`from_env`, its no-argument form),
    which resolves each field from code, then environment variables, then the
    merged config files, then the default (see the module docstring). A
    ``DonkeyConfig(...)`` built directly reads neither env nor files. Change
    fields afterwards with :meth:`with_overrides`.

    The control-plane credential (``client_id`` / ``client_secret``, for the
    Exchange registry) and the LLM-proxy credential (``llm_proxy_*``,
    for model calls) are separate (BG §1.1). Call :meth:`validated` to get every
    missing field for a capability in one error.

    Raises:
        ConfigError: A field holds an invalid value, such as a reserved header
            name.

    Docs: https://docs.donkey-kit.dev/reference/configuration
    """

    # --- Anypoint control plane (registry) ---
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    org_id: str | None = None
    environment: str = "Sandbox"
    region: Region = "us"
    base_url: str | None = None           # override; else derived from region

    # --- LLM proxy (data plane) — SEPARATE credential from the control plane ---
    # Auth is a client_id/client_secret REQUEST-header pair (client-id-enforcement),
    # docs/verified-apis.md §2/§3. NOT a bearer token.
    llm_proxy_url: str | None = None
    llm_proxy_client_id: str | None = None
    llm_proxy_client_secret: str | None = field(default=None, repr=False)
    # Optional: the value for the client's API-key slot, sent to the LLM proxy
    # (``Authorization: Bearer`` from OpenAI-compatible clients, ``x-api-key`` from
    # Anthropic, ``x-goog-api-key`` from ADK's gemini()). A client-id proxy ignores
    # it; leave unset to send a sentinel.
    llm_proxy_key: str | None = field(default=None, repr=False)

    # --- LLM proxy auth mode (BG §1.1, #509) ---
    # Which data-plane ingress the proxy uses. Default ``"client-id"`` (the
    # CIE header pair above). ``"jwt"`` selects the model-wallet
    # ingress: no ``client_secret``, an IdP JWT supplied dynamically via an
    # ``AuthProvider`` (``Donkey(llm_auth=...)``), and a durable wallet-selector
    # client ID sent as the ``X-Client-Id`` header (docs/verified-apis.md §2/§3, #372).
    # ``"bearer"`` sends only ``Authorization: Bearer <token>`` from that provider
    # (#836).
    llm_proxy_auth: LlmProxyAuth = "client-id"
    # The wallet-selector client ID sent as ``X-Client-Id`` in JWT mode — the
    # wallet's system-generated clientId (read from the omni API, see the
    # ddk-configure-llm-proxy-model-wallet skill). Durable, not rotating, so it IS
    # a config field (the rotating JWT is not); required in JWT mode. Ignored in
    # client-id mode.
    llm_proxy_wallet_client_id: str | None = None

    # --- Attribution (real header names: see docs/verified-apis.md §3) ---
    application_name: str | None = None
    business_group: str | None = None

    # --- Correlation request-header NAME overrides (BG §1.1, #195) ---
    # Override the correlation/call-id request-header names (docs/verified-apis.md
    # §3) without a release. Unset → the defaults in ``core/_verify``.
    # These and the ``cost_*_header`` names are checked on construction: see
    # ``core/header_names`` for the names they may not use.
    correlation_header: str | None = None
    call_id_header: str | None = None

    # --- Cost-attribution tags + request-header NAME overrides (docs/verified-apis.md §3, #196) ---
    # The fixed dimensions (team/project/env/enduser.id), set once and emitted on
    # every call. The gateway ingests no cost-tag header (docs/verified-apis.md
    # §3), so these are sent only with ``send_cost_headers``; the ``cost_*_header``
    # overrides rename them — unset → the defaults in ``core/_verify``.
    cost: CostTags = CostTags()
    cost_team_header: str | None = None
    cost_project_header: str | None = None
    cost_env_header: str | None = None
    cost_enduser_header: str | None = None

    # --- Behaviour ---
    timeout_s: float = 60.0
    max_retries: int = 3
    registry_cache_ttl_s: int = 300
    telemetry: bool = True
    # Emit message content (prompts, completions, tool arguments/results) on OTel
    # spans. Default FALSE: the gateway masks PII in ITS logs, but spans are
    # emitted upstream of the gateway, so defaulting this on would re-export the
    # very content the platform just masked to whatever OTLP collector is wired
    # up (#306, BG §1.6). Opting in is the developer assuming that obligation.
    telemetry_capture_content: bool = False
    # Install DDK's OTLP TracerProvider as the process-global OpenTelemetry
    # provider. Default FALSE: OTel lets the global provider be set once, so a
    # library that took it implicitly would lock out a host that configures its
    # own afterwards. Off, DDK exports its own spans through a DDK-scoped
    # provider and defers to any global provider the host sets (#732,
    # docs/adr/0010-no-hidden-global-side-effects.md).
    telemetry_install_global: bool = False
    # What to do when the gateway serves a different model than requested (docs/verified-apis.md §3,
    # #309). Default "off" — the substitution is surfaced passively on
    # ``donkey.last_call``; "raise" opts into a hard ``ModelSubstituted`` error.
    on_model_substitution: OnModelSubstitution = "off"
    # Send the cost tags as request headers too. Default FALSE: nothing on the
    # gateway reads a cost-tag header (docs/verified-apis.md §3, #522), so the
    # ``donkey.cost.*`` span attributes are the only consumer, and an
    # ``enduser.id`` header would carry an end-user identifier for no reader.
    # When enabled, the headers go on data-plane (model) requests only, never on
    # a control-plane client's (#833).
    send_cost_headers: bool = False
    # Re-send a model POST after a 502 or 504. Default FALSE: either status can
    # follow an upstream call that completed and billed, so a re-send can bill
    # twice, and docs/verified-apis.md records no gateway idempotency key that
    # would make it safe (docs/adr/0009-*.md, #728). A 503 still retries, and
    # so does every non-model request.
    retry_model_calls_on_gateway_errors: bool = False

    # --- Provenance (config resolution) ---
    # Where each field was resolved from and a keyed digest of the value it had
    # there, filled in by resolve(). A field with no entry, or whose value no
    # longer matches, was set in code and counts as explicit.
    _sources: Mapping[str, _Loaded] = field(
        default_factory=dict, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        loaded = {name: _as_loaded(name, entry) for name, entry in self._sources.items()}
        object.__setattr__(self, "_sources", loaded)
        self._check_values()
        self._check_header_names()

    def _check_values(self) -> None:
        """Refuse out-of-range numbers, non-boolean switches and unknown choices,
        listing every bad field in one error. Runs on every construction, so
        :meth:`with_overrides` and ``dataclasses.replace`` re-check too (#809)."""
        values = {key: getattr(self, key) for key in _CHECKED_KEYS}
        problems = _value_problems(values, lambda key: _where(self.source_of(key), _ENV_VARS[key]))
        if problems:
            raise _invalid_config(problems)

    def _check_header_names(self) -> None:
        """Refuse a configurable header name that isn't safe to send, or that
        two keys share (see :mod:`donkey_kit.core.header_names`)."""
        in_use = {key: getattr(self, key) or dflt for key, dflt in _HEADER_KEYS}
        for key, _ in _HEADER_KEYS:
            name = getattr(self, key)
            if name is None:
                continue
            problem = header_name_problem(name)
            if problem is None:
                clash = [k for k, n in in_use.items() if k != key and n.lower() == name.lower()]
                if clash:
                    problem = f"{clash[0]} already uses it"
            if problem is not None:
                raise ConfigError(
                    f"{key} names the header {name!r}, which can't be used: {problem}. "
                    f"{key} is set in {_where(self.source_of(key), _ENV_VARS[key])}. "
                    "Choose a different header name."
                )

    # ----------------------------------------------------------------- factory
    @classmethod
    def resolve(
        cls,
        *,
        path: str | os.PathLike[str] | None = None,
        **overrides: Unpack[ConfigOverrides],
    ) -> DonkeyConfig:
        """Resolve every field along the configured precedence (§2.1, #727).

        Highest first, per field: ``overrides`` (set in code), environment
        variables, ``.donkey-kit.local.toml``, ``.donkey-kit.toml``, the user
        file (``$XDG_CONFIG_HOME/.donkey-kit.toml``), then the field default.
        The three files are merged key by key, so a lower file fills whatever a
        higher one leaves unset. ``cost`` merges per dimension: a dimension
        set in ``overrides["cost"]`` wins, the others keep their resolved value.

        Does not check that a capability's required fields are set; call
        :meth:`validated` for that. Every field, and each cost dimension as
        ``cost.<name>``, records its source for :meth:`source_of`; an override
        counts as set in code.

        Args:
            path: A config file to read in place of the working directory's
                ``.donkey-kit.toml``. A ``.donkey-kit.local.toml`` beside it
                overlays it, and the endpoint rule of :meth:`check_endpoints`
                treats both as project files. The user file is still read
                beneath them.
            **overrides: Any :class:`DonkeyConfig` field, by name.

        Raises:
            ConfigError: A value can't be parsed or is out of range (every bad
                value is listed in one error, wherever it was set), ``path``
                names no file, or a config file is malformed.
            TypeError: An override names no :class:`DonkeyConfig` field.

        Docs: https://docs.donkey-kit.dev/reference/configuration#precedence
        """
        given = cast("Mapping[str, object]", overrides)
        unknown = sorted(set(given) - _OVERRIDE_NAMES)
        if unknown:
            raise TypeError(
                "DonkeyConfig.resolve() got unknown field names: "
                f"{', '.join(repr(name) for name in unknown)}. Use DonkeyConfig field names."
            )

        table, file_sources = _load_config_files(path=path)
        defaults = {f.name: f.default for f in fields(cls)}
        values: dict[str, object] = {}
        sources: dict[str, ConfigSource] = {}
        # Values that can't be parsed, by field: reported with the out-of-range
        # ones below, in one error (#809).
        problems: dict[str, str] = {}

        for spec in _FIELDS:
            if spec.name in given:
                # Set in code: no source entry, and the lower layers' value is
                # never read, so an invalid one there can't fail this call.
                values[spec.name] = given[spec.name]
                continue
            raw, source = _pick(spec, table, file_sources)
            sources[spec.name] = source
            if source.kind == "default":
                values[spec.name] = defaults[spec.name]
                continue
            try:
                values[spec.name] = spec.parse(raw)
            except ValueError as exc:
                where = _where(source, spec.env)
                problems[spec.name] = f"{spec.name} is {raw!r}, set in {where}; {exc}"
                values[spec.name] = defaults[spec.name]

        toml_cost = table.get("cost")
        for name, key, env in _COST_KEYS:
            if env in os.environ:
                sources[f"cost.{name}"] = ConfigSource("env")
            elif isinstance(toml_cost, dict) and key in toml_cost:
                sources[f"cost.{name}"] = file_sources[f"cost.{key}"]
            else:
                sources[f"cost.{name}"] = ConfigSource("default")
        cost = _resolve_cost_tags(toml_cost)
        if "cost" in given:
            override = given["cost"]
            if not isinstance(override, CostTags):
                raise ConfigError(
                    f"cost must be a CostTags, got {type(override).__name__}. "
                    "Pass cost=CostTags(team=..., project=...)."
                )
            cost = cost.merge(override)
            for name, _ in override.items():
                del sources[f"cost.{name}"]
        values["cost"] = cost

        parsed = {k: v for k, v in values.items() if k not in problems}
        out_of_range = _value_problems(
            parsed, lambda key: _where(sources.get(key, _EXPLICIT), _ENV_VARS[key])
        )
        if problems or out_of_range:
            raise _invalid_config([*problems.values(), *out_of_range])
        key_id = _key_id()
        loaded = {
            name: _Loaded(src, _digest(_value_at(values, name)), key_id)
            for name, src in sources.items()
        }
        return cls(**cast("ConfigOverrides", values), _sources=loaded)

    @classmethod
    def from_env(cls) -> DonkeyConfig:
        """Build from env + the optional config files: :meth:`resolve` with no
        arguments (see the module docstring). Does not validate; call
        :meth:`validated` when you know which capability you need."""
        return cls.resolve()

    # --------------------------------------------------------------- derived
    @property
    def control_plane_url(self) -> str:
        """The Anypoint control-plane base URL — explicit override or region.

        A region whose host is unconfirmed (docs/verified-apis.md §1) warns once
        with an ``UnverifiedValueWarning``; set ``base_url`` to silence it."""
        if self.base_url:
            return self.base_url
        unconfirmed = UNVERIFIED_REGION_HOSTS.get(self.region)
        return unconfirmed.get() if unconfirmed else REGION_HOSTS[self.region]

    def source_of(self, name: str) -> ConfigSource:
        """Where field ``name`` was resolved from. ``explicit`` (set in code) when
        :meth:`resolve` did not resolve it or its value has changed since, e.g.
        through ``dataclasses.replace``, :meth:`with_overrides` or direct
        construction; copies that keep the value keep the label, and so does
        ``DonkeyConfig(**dataclasses.asdict(cfg))`` in the same process. A label
        recorded in another process can't be checked against the value: an
        endpoint keeps it and a credential counts as set in code."""
        loaded = self._sources.get(name)
        if loaded is None:
            return _EXPLICIT
        if loaded.key_id != _key_id():
            return loaded.source if name in _ENDPOINT_KEYS else _EXPLICIT
        if not hmac.compare_digest(_digest(_value_at(self, name)), loaded.digest):
            return _EXPLICIT
        return loaded.source

    def with_overrides(self, **kw: Unpack[ConfigOverrides]) -> DonkeyConfig:
        """Return a copy with the given fields replaced.

        Each overridden field counts as set in code, which matters to
        :meth:`check_endpoints`. The copy is checked like any construction, so
        an invalid value raises :class:`ConfigError` (#809).
        """
        sources = {k: v for k, v in self._sources.items() if k not in kw}
        return replace(self, _sources=sources, **kw)

    # ------------------------------------------------------------- validation
    def validated(self, *, need: Capability = "control_plane") -> DonkeyConfig:
        """Return self if valid for the requested capability, else raise a
        :class:`ConfigError` listing EVERY missing field at once.

        ``need`` is one of ``"control_plane"`` (registry) or
        ``"llm"`` (proxy). The two credentials are independent (BG §1.1): a user
        may legitimately have proxy access and no Exchange access.

        Once nothing is missing, the endpoint is checked too (:meth:`check_endpoints`).
        """

        missing = self.missing_fields(need=need)
        if missing:
            joined = "\n  - ".join(missing)
            raise ConfigError(
                f"Configuration for {need!r} is incomplete. Missing:\n  - {joined}\n"
                f"Set them via kwargs, environment variables, or {TOML_NAME} "
                f"(secrets in {LOCAL_TOML_NAME})."
            )
        self.check_endpoints(need=need)
        return self

    def missing_fields(self, *, need: Capability) -> list[str]:
        """Every required field for ``need`` that is unset, each with the env var
        that sets it. The list :meth:`validated` reports."""
        missing: list[str] = []
        if need == "control_plane":
            if not self.client_id:
                missing.append(_needs("client_id"))
            if not self.client_secret:
                missing.append(_needs("client_secret"))
            if not self.org_id:
                missing.append(_needs("org_id"))
        elif need == "llm":
            if not self.llm_proxy_url:
                missing.append(_needs("llm_proxy_url"))
            if self.llm_proxy_auth == "jwt":
                # Model-wallet ingress (#509): NO client_secret — CIE is disabled
                # and the caller is identified from the JWT. The rotating JWT is
                # supplied via an AuthProvider (Donkey(llm_auth=...)), not config,
                # so it is not a "missing field" here — the provider-attached check
                # lives where the provider is known (LLMClient.client()). What IS
                # required here is the durable wallet-selector client ID.
                if not self.llm_proxy_wallet_client_id:
                    missing.append(
                        _needs("llm_proxy_wallet_client_id")
                        + " — the wallet-selector X-Client-Id, required in jwt auth mode"
                    )
            elif self.llm_proxy_auth == "client-id":
                # client-id enforcement (default): the CIE pair. In bearer mode
                # (#836) only the URL is required: the token comes from the
                # llm_auth provider, checked where the provider is known.
                if not self.llm_proxy_client_id:
                    missing.append(_needs("llm_proxy_client_id"))
                if not self.llm_proxy_client_secret:
                    missing.append(_needs("llm_proxy_client_secret"))
        else:
            raise ConfigError(f"Unknown capability {need!r} passed to validated().")
        return missing

    def check_endpoints(self, *, need: Capability, code_credential: str | None = None) -> None:
        """Raise :class:`ConfigError` if ``need``'s endpoint may not receive the
        credentials that would be sent to it.

        The endpoint must be ``https://``; ``http://`` is accepted for loopback
        hosts, and for other hosts only with ``DONKEY_ALLOW_HTTP=1`` in the
        environment, which does not relax the rest of this check. And an
        endpoint read from the working directory's ``.donkey-kit.toml`` or its
        ``.local`` overlay only receives credentials read from those same files,
        unless it is a standard Anypoint control-plane host or
        ``DONKEY_TRUST_PROJECT_CONFIG=1`` is set in the environment. Loopback
        hosts are exempt from the ``https://`` rule only. In ``jwt`` and
        ``bearer`` auth modes the token comes from the caller's ``AuthProvider``,
        never from those files, so it always counts as outside them. ``code_credential``
        names another credential supplied in code, such as the token of a
        ``Donkey(auth=...)`` provider, which counts as outside them too.
        """
        runtime_credential = code_credential
        if need == "control_plane":
            key, url = "base_url", self.control_plane_url
            credentials: tuple[str, ...] = ("client_id", "client_secret")
        elif need == "llm":
            if not self.llm_proxy_url:
                return
            key, url = "llm_proxy_url", self.llm_proxy_url
            if self.llm_proxy_auth == "jwt":
                credentials = ("llm_proxy_wallet_client_id",)
                runtime_credential = "the JWT from the llm_auth provider"
            elif self.llm_proxy_auth == "bearer":
                credentials = ("llm_proxy_key",)
                runtime_credential = "the token from the llm_auth provider"
            else:
                credentials = ("llm_proxy_client_id", "llm_proxy_client_secret", "llm_proxy_key")
        else:
            raise ConfigError(f"Unknown capability {need!r} passed to check_endpoints().")

        require_secure_url(url, name=key)
        origin = self.source_of(key)
        if origin.kind not in _WORKDIR_KINDS:
            return
        if need == "control_plane" and host_of(url) in STANDARD_CONTROL_PLANE_HOSTS:
            return
        # An unrecognised value leaves this switch off, like DONKEY_ALLOW_HTTP.
        if os.environ.get(TRUST_PROJECT_CONFIG_ENV, "").strip().lower() in _TRUE_TOKENS:
            return
        outside = [
            f"{name} (from {self.source_of(name)})"
            for name in credentials
            if getattr(self, name) and self.source_of(name).kind not in _WORKDIR_KINDS
        ]
        if runtime_credential:
            outside.append(runtime_credential)
        if outside:
            raise _binding_error(
                key=key,
                env_var=_ENV_VARS[key],
                url=url,
                origin=origin,
                credentials=outside,
                offer_local_file=runtime_credential is None,
            )


def _binding_error(
    *,
    key: str,
    env_var: str,
    url: str,
    origin: ConfigSource,
    credentials: list[str],
    offer_local_file: bool,
) -> ConfigError:
    """The error for a working-directory endpoint paired with outside credentials:
    names the file, key and host, and lists the ways to resolve it."""
    assert origin.path is not None  # file sources always carry their path
    options = [f"set the URL in the environment instead ({env_var}=https://...)"]
    if offer_local_file:
        local = origin.path.parent / LOCAL_TOML_NAME
        options.append(f"keep the credentials in {local}, next to the project file")
    options.append(
        f"trust the project config files by setting {TRUST_PROJECT_CONFIG_ENV}=1"
    )
    return ConfigError(
        f"Not sending {', '.join(credentials)} to {host_of(url)}: {key} is set in "
        f"{origin.path}, and credentials from outside the project config files "
        "are only sent to hosts those files name when you opt in. To continue, "
        "do one of:\n  - " + "\n  - ".join(options)
    )


def _needs(key: str) -> str:
    """A missing field as :meth:`DonkeyConfig.missing_fields` reports it."""
    return f"{key} (env {_ENV_VARS[key]})"


def _where(source: ConfigSource, env_var: str) -> str:
    """Where a key was set, for an error message: the file, env var, or code."""
    if source.path is not None:
        return str(source.path)
    if source.kind == "env":
        return f"the environment ({env_var})"
    return "code"


def _value_at(root: object, name: str) -> object:
    """The value of field ``name`` on a config or a mapping of field values;
    ``cost.team`` reads a cost dimension."""
    first, *rest = name.split(".")
    value = root[first] if isinstance(root, Mapping) else getattr(root, first)
    for part in rest:
        value = getattr(value, part)
    return value


def _opt(v: object) -> str | None:
    return None if v is None else str(v)


# The four cost dimensions: field name, [donkey.cost] key and the env var that
# overrides each, in field order.
_COST_KEYS: tuple[tuple[str, str, str], ...] = (
    ("team", "team", "DONKEY_COST_TEAM"),
    ("project", "project", "DONKEY_COST_PROJECT"),
    ("env", "env", "DONKEY_COST_ENV"),
    ("enduser_id", "enduser.id", "DONKEY_COST_ENDUSER_ID"),
)


def _resolve_cost_tags(toml_cost: object) -> CostTags:
    """Resolve the cost tags along the fixed precedence: ``[donkey.cost]`` toml
    table is the base (its keys validated against the fixed set — an unknown
    dimension raises :class:`ConfigError`, never a silent drop, #196 AC #1), then
    ``DONKEY_COST_*`` env vars override per dimension. Absent both → empty tags."""
    if toml_cost is None:
        base = CostTags()
    elif isinstance(toml_cost, dict):
        base = CostTags.from_mapping(toml_cost, source="[donkey.cost]")
    else:
        raise ConfigError("[donkey.cost] must be a table of cost-attribution tags.")
    env_over = {name: os.environ[var] for name, _, var in _COST_KEYS if var in os.environ}
    return base.merge(CostTags(**env_over)) if env_over else base


def _as_int(v: object) -> int:
    if not isinstance(v, bool):
        try:
            return int(str(v).strip())
        except ValueError:
            pass
    raise ValueError("expected a whole number")


def _as_float(v: object) -> float:
    if not isinstance(v, bool):
        try:
            return float(str(v).strip())
        except ValueError:
            pass
    raise ValueError("expected a number")


def _as_bool(v: object) -> bool:
    """A switch, parsed strictly: an unrecognised value such as ``"flase"`` is an
    error, never a silent False (#809)."""
    if isinstance(v, bool):
        return v
    token = str(v).strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise ValueError(f"expected one of {', '.join(_TRUE_TOKENS + _FALSE_TOKENS)}")


def missing_llm_auth_error(mode: LlmProxyAuth) -> ConfigError:
    """The error for a token auth mode (:data:`TOKEN_AUTH_MODES`) used with no
    ``llm_auth`` provider attached to the shared data-plane client, which is
    also the case for the module-level factories (#509, #836)."""
    token = "model-wallet JWT" if mode == "jwt" else "bearer token"
    return ConfigError(
        f"llm_proxy_auth={mode!r} requires an AuthProvider that supplies the "
        f"{token}, but none is attached. Pass one when constructing Donkey, e.g. "
        "`Donkey(llm_auth=StaticToken(token))` or a custom AuthProvider that "
        "refreshes the token (see donkey_kit.core.auth). The module-level "
        "factories have no provider; use a Donkey instance instead."
    )


def _as_token(v: object) -> str:
    """A choice value, case-insensitive; :func:`_value_problems` checks it."""
    return str(v).strip().lower()


def _one_of(*choices: str) -> Callable[[object], str | None]:
    """A check that accepts exactly ``choices``.

    An unknown choice is reported, never replaced by the default: a silent
    fall-back would leave a caller who typed ``llm_proxy_auth="oauth"`` believing
    they had selected the wallet ingress (BG §1.1, #509), or
    ``on_model_substitution="error"`` believing they had opted into strictness
    (#309)."""
    expected = "one of " + ", ".join(repr(c) for c in choices)

    def check(value: object) -> str | None:
        return None if value in choices else expected

    return check


def _positive_number(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        return "a number of seconds greater than 0"
    return None


def _non_negative_int(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return "a whole number, 0 or more"
    return None


def _is_bool(value: object) -> str | None:
    return None if isinstance(value, bool) else "True or False"


@dataclass(frozen=True)
class _Field:
    """One row of the field table: how a :class:`DonkeyConfig` field is read.

    ``parse`` turns the raw environment or TOML value into the field's type,
    raising ``ValueError`` when it can't; ``check``, when set, returns what the
    field expects if a value (from any layer, code included) is out of range.
    The default is the dataclass field's own."""

    name: str
    env: str
    parse: Callable[[object], object]
    check: Callable[[object], str | None] | None = None

    @property
    def toml_key(self) -> str:
        """The key in a ``[donkey]`` table: always the field name."""
        return self.name


# The field table (#720, #727): every DonkeyConfig field except ``cost``, which
# is set per dimension instead (``_COST_KEYS``). resolve(), missing_fields(),
# the value checks and the error messages all read it, and unit tests pin it
# against the dataclass and the configuration docs.
_FIELDS: tuple[_Field, ...] = (
    _Field("client_id", "ANYPOINT_CLIENT_ID", _opt),
    _Field("client_secret", "ANYPOINT_CLIENT_SECRET", _opt),
    _Field("org_id", "ANYPOINT_ORG_ID", _opt),
    _Field("environment", "ANYPOINT_ENV", str),
    _Field("region", "ANYPOINT_REGION", str, _one_of(*sorted(REGION_HOSTS))),
    _Field("base_url", "ANYPOINT_BASE_URL", _opt),
    _Field("llm_proxy_url", "DONKEY_LLM_PROXY_URL", _opt),
    _Field("llm_proxy_client_id", "DONKEY_LLM_PROXY_CLIENT_ID", _opt),
    _Field("llm_proxy_client_secret", "DONKEY_LLM_PROXY_CLIENT_SECRET", _opt),
    _Field("llm_proxy_key", "DONKEY_LLM_PROXY_KEY", _opt),
    _Field(
        "llm_proxy_auth", "DONKEY_LLM_PROXY_AUTH", _as_token, _one_of("client-id", "jwt", "bearer")
    ),
    _Field("llm_proxy_wallet_client_id", "DONKEY_LLM_PROXY_WALLET_CLIENT_ID", _opt),
    _Field("application_name", "DONKEY_APP_NAME", _opt),
    _Field("business_group", "DONKEY_BUSINESS_GROUP", _opt),
    _Field("correlation_header", "DONKEY_CORRELATION_HEADER", _opt),
    _Field("call_id_header", "DONKEY_CALL_ID_HEADER", _opt),
    _Field("cost_team_header", "DONKEY_COST_TEAM_HEADER", _opt),
    _Field("cost_project_header", "DONKEY_COST_PROJECT_HEADER", _opt),
    _Field("cost_env_header", "DONKEY_COST_ENV_HEADER", _opt),
    _Field("cost_enduser_header", "DONKEY_COST_ENDUSER_HEADER", _opt),
    _Field("timeout_s", "DONKEY_TIMEOUT_S", _as_float, _positive_number),
    _Field("max_retries", "DONKEY_MAX_RETRIES", _as_int, _non_negative_int),
    _Field("registry_cache_ttl_s", "DONKEY_REGISTRY_CACHE_TTL_S", _as_int, _non_negative_int),
    _Field("telemetry", "DONKEY_TELEMETRY", _as_bool, _is_bool),
    _Field("telemetry_capture_content", "DONKEY_TELEMETRY_CAPTURE_CONTENT", _as_bool, _is_bool),
    _Field("telemetry_install_global", "DONKEY_TELEMETRY_INSTALL_GLOBAL", _as_bool, _is_bool),
    _Field(
        "on_model_substitution", "DONKEY_ON_MODEL_SUBSTITUTION", _as_token, _one_of("off", "raise")
    ),
    _Field("send_cost_headers", "DONKEY_SEND_COST_HEADERS", _as_bool, _is_bool),
    _Field(
        "retry_model_calls_on_gateway_errors",
        "DONKEY_RETRY_MODEL_CALLS_ON_GATEWAY_ERRORS",
        _as_bool,
        _is_bool,
    ),
)

# Views of the table.
_ENV_VARS: dict[str, str] = {spec.name: spec.env for spec in _FIELDS}
_CHECKED_KEYS: tuple[str, ...] = tuple(spec.name for spec in _FIELDS if spec.check is not None)
_OVERRIDE_NAMES: frozenset[str] = frozenset(_ENV_VARS) | {"cost"}


def _pick(
    spec: _Field, table: Mapping[str, object], file_sources: Mapping[str, ConfigSource]
) -> tuple[object, ConfigSource]:
    """The raw value of ``spec`` from the highest layer below code that sets
    it, with its source: the environment, then the merged files. ``None`` with
    a ``default`` source when neither sets it."""
    if spec.env in os.environ:
        return os.environ[spec.env], ConfigSource("env")
    if spec.toml_key in table:
        return table[spec.toml_key], file_sources[spec.toml_key]
    return None, ConfigSource("default")


def _value_problems(values: Mapping[str, object], where: Callable[[str], str]) -> list[str]:
    """One line for each checked field in ``values`` that is out of range, not a
    boolean, or not an allowed choice, in field-table order; ``where(key)`` says
    where it was set."""
    problems: list[str] = []
    for spec in _FIELDS:
        if spec.check is None or spec.name not in values:
            continue
        expected = spec.check(values[spec.name])
        if expected is not None:
            problems.append(
                f"{spec.name} is {values[spec.name]!r}, set in {where(spec.name)}; "
                f"expected {expected}"
            )
    return problems


def _invalid_config(problems: list[str]) -> ConfigError:
    joined = "\n  - ".join(problems)
    return ConfigError(
        f"Configuration is invalid:\n  - {joined}\n"
        f"Fix each value where it is set: in code, an environment variable, or {TOML_NAME}."
    )


def _load_config_files(
    name: str = "donkey", *, path: str | os.PathLike[str] | None = None
) -> tuple[dict[str, object], dict[str, ConfigSource]]:
    """The merged ``[<name>]`` table (``[donkey]`` by default) and the source of
    every key in it, by dotted path (``cost.team``).

    The files merge key by key in :data:`_PRECEDENCE` order (#727): the user
    file (:func:`_user_config_file`) at the bottom, then the working
    directory's ``.donkey-kit.toml``, then its ``.donkey-kit.local.toml``.
    ``path`` names a file to read in place of the working directory's
    ``.donkey-kit.toml``; the ``.donkey-kit.local.toml`` beside it is then the
    overlay. Missing files are fine, except ``path``; a malformed file raises,
    and so does a working-directory file that resolves (through a link)
    outside it."""

    layers: list[tuple[ConfigSource, dict[str, object]]] = []
    user = _user_config_file()
    if user is not None and user.is_file():
        layers.append((ConfigSource("user", user), _read_table(user, name)))

    if path is None:
        directory = Path.cwd()
        project = directory / TOML_NAME
        if project.is_file():
            _require_inside(project, directory)
    else:
        project = Path(path).absolute()
        if not project.is_file():
            raise ConfigError(
                f"There is no config file at {project}. Check the path, or leave it "
                f"out to read {TOML_NAME} in the working directory."
            )
        directory = project.parent
    if project.is_file():
        table = _read_table(project, name)
        if name == "donkey":
            _warn_on_secrets(project, table)
        layers.append((ConfigSource("project", project), table))
    local = directory / LOCAL_TOML_NAME
    if local.is_file() and local.resolve() != project.resolve():
        _require_inside(local, directory)
        layers.append((ConfigSource("local", local), _read_table(local, name)))

    # Lowest layer first, so each higher one is merged over it.
    layers.sort(key=lambda layer: _PRECEDENCE.index(layer[0].kind), reverse=True)
    merged: dict[str, object] = {}
    sources: dict[str, ConfigSource] = {}
    for source, table in layers:
        _merge_table(merged, table, source, sources, prefix="")
    return merged, sources


def _user_config_file() -> Path | None:
    """``$XDG_CONFIG_HOME/.donkey-kit.toml``, or ``~/.config/.donkey-kit.toml``
    when the variable is unset, empty, or relative — the XDG Base Directory
    default (#837). ``None`` when no home directory can be determined."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if os.path.isabs(xdg):
        return Path(xdg) / TOML_NAME
    try:
        return Path.home() / ".config" / TOML_NAME
    except RuntimeError:
        return None


def _require_inside(path: Path, cwd: Path) -> None:
    """Refuse a working-directory config file whose real location is elsewhere:
    its keys would be labelled as the working directory's."""
    target = path.resolve()
    if not target.is_relative_to(cwd.resolve()):
        raise ConfigError(
            f"{path} links to {target}, outside the working directory. Replace the "
            "link with a regular file in the working directory, or set those "
            "values in the environment."
        )


def _merge_table(
    into: dict[str, object],
    table: dict[str, object],
    source: ConfigSource,
    sources: dict[str, ConfigSource],
    *,
    prefix: str,
) -> None:
    """Merge ``table`` over ``into``: tables merge recursively, anything else
    (scalars, arrays) replaces. Records ``source`` for every key it sets."""
    for key, value in table.items():
        path = f"{prefix}{key}"
        sources[path] = source
        current = into.get(key)
        if isinstance(value, dict):
            nested = dict(current) if isinstance(current, dict) else {}
            _merge_table(nested, value, source, sources, prefix=f"{path}.")
            into[key] = nested
        else:
            for stale in [p for p in sources if p.startswith(f"{path}.")]:
                del sources[stale]
            into[key] = value


def _read_table(path: Path, name: str = "donkey") -> dict[str, object]:
    """Read the ``[<name>]`` table. For ``[donkey]``, keep only keys that are
    real config fields."""
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Malformed {path}: {exc}") from exc
    table = data.get(name, {})
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [{name}] must be a table.")
    if name != "donkey":
        return table
    known = {f.name for f in fields(DonkeyConfig) if not f.name.startswith("_")}
    return {k: v for k, v in table.items() if k in known}


def _warn_on_secrets(path: Path, table: dict[str, object]) -> None:
    found = [key for key in _SECRET_KEYS if key in table]
    if found:
        warnings.warn(
            f"{path} contains {', '.join(found)}. Keep secrets out of the committed "
            f"project file: move them to {LOCAL_TOML_NAME} next to it (and gitignore "
            "it) or to environment variables.",
            ConfigWarning,
            stacklevel=_caller_stacklevel(),
        )


def _caller_stacklevel() -> int:
    """The ``warnings.warn`` stacklevel, called from the function that warns, of
    the first frame outside ``donkey_kit``: the user's call to ``resolve()``,
    ``from_env()`` or ``Donkey.from_env()``, however many SDK frames sit between."""
    frame = inspect.currentframe()
    frame = frame.f_back if frame is not None else None  # the function that warns
    level = 1
    while frame is not None and frame.f_globals.get("__name__", "").split(".")[0] == "donkey_kit":
        frame = frame.f_back
        level += 1
    return level

"""Configuration.

Resolution order: explicit kwarg → env var → config file → default. The config
file is the working directory's ``.donkey-kit.toml`` with its gitignored
``.donkey-kit.local.toml`` overlaid on top; if neither exists,
``$XDG_CONFIG_HOME/.donkey-kit.toml``. We never read ``.env`` implicitly — the
user calls ``load_dotenv()`` themselves.

Every resolved field records its source, and :meth:`DonkeyConfig.check_endpoints`
uses it so an endpoint read from the working directory's files only receives
credentials read from those same files (``DONKEY_TRUST_PROJECT_CONFIG=1`` opts
out). Endpoints must be ``https://``, except loopback hosts, or any host when
``DONKEY_ALLOW_HTTP=1`` is set in the environment.

``validated()`` reports ALL missing fields in one error, not one per run — the
one-missing-variable-per-run loop is the most common first-five-minutes
abandonment (config resolution).
"""

from __future__ import annotations

import os
import sys
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Literal, cast

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 has no stdlib tomllib; the [core] dep ``tomli`` backfills it.
    import tomli as tomllib

from ._verify import REGION_HOSTS
from .cost import CostTags
from .endpoints import STANDARD_CONTROL_PLANE_HOSTS, host_of, require_secure_url
from .errors import ConfigError, ConfigWarning

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
# ``"client-id"`` (default): the LIVE-VERIFIED ``client_id``/``client_secret``
# request-header pair (client-id-enforcement, docs/verified-apis.md §2/§3). ``"jwt"``:
# a wallet-backed proxy where Client ID Enforcement is disabled and the caller is
# identified from an IdP-issued JWT validated by the JWT Validation policy, with
# NO ``client_secret`` — the parallel ingress captured live under #372
# (docs/verified-apis.md §2/§3). The mode is durable config even though the JWT itself is
# not: the rotating credential enters through an ``AuthProvider`` (see
# ``Donkey(llm_auth=...)``), never a config field. Inferring the mode from "no
# client_id set" is deliberately NOT done — it would turn a typo into a silent
# mode switch and make the per-mode missing-field report misleading (#509).
LlmProxyAuth = Literal["client-id", "jwt"]

_TOML_NAME = ".donkey-kit.toml"
_LOCAL_TOML_NAME = ".donkey-kit.local.toml"
TRUST_PROJECT_CONFIG_ENV = "DONKEY_TRUST_PROJECT_CONFIG"

SourceKind = Literal["explicit", "env", "project", "local", "user", "default"]

_SOURCE_LABELS: dict[SourceKind, str] = {
    "explicit": "explicit",
    "env": "env",
    "project": "project file",
    "local": "local overlay",
    "user": "user file",
    "default": "default",
}

# The working-directory files. An endpoint read from one of these only receives
# credentials read from one of these (see DonkeyConfig.check_endpoints).
_WORKDIR_KINDS: frozenset[SourceKind] = frozenset({"project", "local"})

# Keys that should never sit in the committed project file.
_SECRET_KEYS = ("client_secret", "llm_proxy_client_secret", "llm_proxy_key")


@dataclass(frozen=True)
class ConfigSource:
    """Where one resolved config field came from. ``path`` is set for file sources."""

    kind: SourceKind
    path: Path | None = None

    def __str__(self) -> str:
        return _SOURCE_LABELS[self.kind]


_EXPLICIT = ConfigSource("explicit")


@dataclass(frozen=True)
class DonkeyConfig:
    # --- Anypoint control plane (registry + provisioning) ---
    client_id: str | None = None          # env: ANYPOINT_CLIENT_ID
    client_secret: str | None = field(default=None, repr=False)  # env: ANYPOINT_CLIENT_SECRET
    org_id: str | None = None             # env: ANYPOINT_ORG_ID
    environment: str = "Sandbox"          # env: ANYPOINT_ENV
    region: Region = "us"                 # env: ANYPOINT_REGION
    base_url: str | None = None           # override; else derived from region

    # --- LLM proxy (data plane) — SEPARATE credential from the control plane ---
    # Auth is a client_id/client_secret REQUEST-header pair (client-id-enforcement),
    # LIVE-VERIFIED — docs/verified-apis.md §2/§3. NOT a bearer token.
    llm_proxy_url: str | None = None            # env: DONKEY_LLM_PROXY_URL
    llm_proxy_client_id: str | None = None      # env: DONKEY_LLM_PROXY_CLIENT_ID
    llm_proxy_client_secret: str | None = field(  # env: DONKEY_LLM_PROXY_CLIENT_SECRET
        default=None, repr=False
    )
    # Optional: fills the OpenAI SDK's mandatory ``api_key`` slot only. The proxy
    # authenticates on the client_id/secret headers above and ignores the bearer,
    # so this is rarely needed; leave unset to use a sentinel.
    llm_proxy_key: str | None = field(default=None, repr=False)  # env: DONKEY_LLM_PROXY_KEY

    # --- LLM proxy auth mode (BG §1.1, #509) ---
    # Which data-plane ingress the proxy uses. Default ``"client-id"`` (the
    # LIVE-VERIFIED CIE header pair above). ``"jwt"`` selects the model-wallet
    # ingress: no ``client_secret``, an IdP JWT supplied dynamically via an
    # ``AuthProvider`` (``Donkey(llm_auth=...)``), and a durable wallet-selector
    # client ID sent as the ``X-Client-Id`` header (docs/verified-apis.md §2/§3, #372).
    llm_proxy_auth: LlmProxyAuth = "client-id"  # env: DONKEY_LLM_PROXY_AUTH
    # The wallet-selector client ID sent as ``X-Client-Id`` in JWT mode — the
    # wallet's system-generated clientId (read from the omni API, see the
    # ddk-configure-llm-proxy-model-wallet skill). Durable, not rotating, so it IS
    # a config field (the rotating JWT is not); required in JWT mode. Ignored in
    # client-id mode.
    llm_proxy_wallet_client_id: str | None = None  # env: DONKEY_LLM_PROXY_WALLET_CLIENT_ID

    # --- Attribution (real header names: see docs/verified-apis.md §3) ---
    application_name: str | None = None   # env: DONKEY_APP_NAME
    business_group: str | None = None     # env: DONKEY_BUSINESS_GROUP

    # --- Correlation request-header NAME overrides (BG §1.1, #195) ---
    # The gateway's inbound correlation/call-id header names are UNVERIFIED
    # (docs/verified-apis.md §3); these let a customer point them at the real names without a
    # release. Unset → the loud ``Unverified`` placeholders in ``core/_verify``.
    correlation_header: str | None = None  # env: DONKEY_CORRELATION_HEADER
    call_id_header: str | None = None      # env: DONKEY_CALL_ID_HEADER

    # --- Cost-attribution tags + request-header NAME overrides (docs/verified-apis.md §3, #196) ---
    # The fixed dimensions (team/project/env/enduser.id), set once and emitted on
    # every call. The gateway-side header names are the highest-priority unknown
    # (docs/verified-apis.md §3); the ``cost_*_header`` overrides let a customer point them at the
    # real names — unset → the loud ``Unverified`` placeholders in ``core/_verify``.
    cost: CostTags = CostTags()            # env: DONKEY_COST_{TEAM,PROJECT,ENV,ENDUSER_ID}
    cost_team_header: str | None = None    # env: DONKEY_COST_TEAM_HEADER
    cost_project_header: str | None = None  # env: DONKEY_COST_PROJECT_HEADER
    cost_env_header: str | None = None     # env: DONKEY_COST_ENV_HEADER
    cost_enduser_header: str | None = None  # env: DONKEY_COST_ENDUSER_HEADER

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
    # What to do when the gateway serves a different model than requested (docs/verified-apis.md §3,
    # #309). Default "off" — the substitution is surfaced passively on
    # ``donkey.last_call``; "raise" opts into a hard ``ModelSubstituted`` error.
    on_model_substitution: OnModelSubstitution = "off"
    # Send the cost tags as request headers too. Default FALSE: nothing on the
    # gateway reads a cost-tag header (docs/verified-apis.md §3, #522), so the
    # ``donkey.cost.*`` span attributes are the only consumer, and an
    # ``enduser.id`` header would carry an end-user identifier for no reader.
    send_cost_headers: bool = False  # env: DONKEY_SEND_COST_HEADERS

    # --- Provenance (config resolution) ---
    # Where each field was resolved from, filled in by from_env(). A field with
    # no entry was set in code and counts as explicit.
    _sources: Mapping[str, ConfigSource] = field(
        default_factory=dict, repr=False, compare=False
    )

    # ----------------------------------------------------------------- factory
    @classmethod
    def from_env(cls) -> DonkeyConfig:
        """Build from env + optional ``.donkey-kit.toml``. Does not validate;
        call :meth:`validated` when you know which capability you need."""

        layers = _load_layers()
        sources: dict[str, ConfigSource] = {}

        def pick(env: str, key: str, default: object) -> object:
            if env in os.environ:
                sources[key] = ConfigSource("env")
                return os.environ[env]
            for source, table in layers:
                if key in table:
                    sources[key] = source
                    return table[key]
            sources[key] = ConfigSource("default")
            return default

        toml_cost = next((table["cost"] for _, table in layers if "cost" in table), None)

        region = str(pick("ANYPOINT_REGION", "region", "us"))
        if region not in REGION_HOSTS:
            raise ConfigError(
                f"Unknown region {region!r}. Expected one of {sorted(REGION_HOSTS)}."
            )

        return cls(
            cost=_resolve_cost_tags(toml_cost),
            client_id=_opt(pick("ANYPOINT_CLIENT_ID", "client_id", None)),
            client_secret=_opt(pick("ANYPOINT_CLIENT_SECRET", "client_secret", None)),
            org_id=_opt(pick("ANYPOINT_ORG_ID", "org_id", None)),
            environment=str(pick("ANYPOINT_ENV", "environment", "Sandbox")),
            region=cast(Region, region),
            base_url=_opt(pick("ANYPOINT_BASE_URL", "base_url", None)),
            llm_proxy_url=_opt(pick("DONKEY_LLM_PROXY_URL", "llm_proxy_url", None)),
            llm_proxy_client_id=_opt(
                pick("DONKEY_LLM_PROXY_CLIENT_ID", "llm_proxy_client_id", None)
            ),
            llm_proxy_client_secret=_opt(
                pick("DONKEY_LLM_PROXY_CLIENT_SECRET", "llm_proxy_client_secret", None)
            ),
            llm_proxy_key=_opt(pick("DONKEY_LLM_PROXY_KEY", "llm_proxy_key", None)),
            llm_proxy_auth=_as_llm_proxy_auth(
                pick("DONKEY_LLM_PROXY_AUTH", "llm_proxy_auth", "client-id")
            ),
            llm_proxy_wallet_client_id=_opt(
                pick("DONKEY_LLM_PROXY_WALLET_CLIENT_ID", "llm_proxy_wallet_client_id", None)
            ),
            application_name=_opt(pick("DONKEY_APP_NAME", "application_name", None)),
            business_group=_opt(pick("DONKEY_BUSINESS_GROUP", "business_group", None)),
            correlation_header=_opt(
                pick("DONKEY_CORRELATION_HEADER", "correlation_header", None)
            ),
            call_id_header=_opt(pick("DONKEY_CALL_ID_HEADER", "call_id_header", None)),
            cost_team_header=_opt(
                pick("DONKEY_COST_TEAM_HEADER", "cost_team_header", None)
            ),
            cost_project_header=_opt(
                pick("DONKEY_COST_PROJECT_HEADER", "cost_project_header", None)
            ),
            cost_env_header=_opt(pick("DONKEY_COST_ENV_HEADER", "cost_env_header", None)),
            cost_enduser_header=_opt(
                pick("DONKEY_COST_ENDUSER_HEADER", "cost_enduser_header", None)
            ),
            timeout_s=_as_float(pick("DONKEY_TIMEOUT_S", "timeout_s", 60.0)),
            max_retries=_as_int(pick("DONKEY_MAX_RETRIES", "max_retries", 3)),
            registry_cache_ttl_s=_as_int(
                pick("DONKEY_REGISTRY_CACHE_TTL_S", "registry_cache_ttl_s", 300)
            ),
            telemetry=_as_bool(pick("DONKEY_TELEMETRY", "telemetry", True)),
            telemetry_capture_content=_as_bool(
                pick("DONKEY_TELEMETRY_CAPTURE_CONTENT", "telemetry_capture_content", False)
            ),
            on_model_substitution=_as_substitution(
                pick("DONKEY_ON_MODEL_SUBSTITUTION", "on_model_substitution", "off")
            ),
            send_cost_headers=_as_bool(
                pick("DONKEY_SEND_COST_HEADERS", "send_cost_headers", False)
            ),
            _sources=sources,
        )

    # --------------------------------------------------------------- derived
    @property
    def control_plane_url(self) -> str:
        """The Anypoint control-plane base URL — explicit override or region."""
        return self.base_url or REGION_HOSTS[self.region]

    def source_of(self, name: str) -> ConfigSource:
        """Where field ``name`` was resolved from (``explicit`` if set in code)."""
        return self._sources.get(name, _EXPLICIT)

    def with_overrides(self, **kw: object) -> DonkeyConfig:
        sources = {**self._sources, **dict.fromkeys(kw, _EXPLICIT)}
        return replace(self, _sources=sources, **kw)  # type: ignore[arg-type]

    # ------------------------------------------------------------- validation
    def validated(self, *, need: str = "control_plane") -> DonkeyConfig:
        """Return self if valid for the requested capability, else raise a
        :class:`ConfigError` listing EVERY missing field at once.

        ``need`` is one of ``"control_plane"`` (registry/provisioning) or
        ``"llm"`` (proxy). The two credentials are independent (BG §1.1): a user
        may legitimately have proxy access and no Exchange access.

        Once nothing is missing, the endpoint is checked too (:meth:`check_endpoints`).
        """

        missing = self.missing_fields(need=need)
        if missing:
            joined = "\n  - ".join(missing)
            raise ConfigError(
                f"Configuration for {need!r} is incomplete. Missing:\n  - {joined}\n"
                f"Set them via kwargs, environment variables, or {_TOML_NAME} "
                f"(secrets in {_LOCAL_TOML_NAME})."
            )
        self.check_endpoints(need=need)
        return self

    def missing_fields(self, *, need: str) -> list[str]:
        """Every required field for ``need`` that is unset, each with the env var
        that sets it. The list :meth:`validated` reports."""
        missing: list[str] = []
        if need == "control_plane":
            if not self.client_id:
                missing.append("client_id (env ANYPOINT_CLIENT_ID)")
            if not self.client_secret:
                missing.append("client_secret (env ANYPOINT_CLIENT_SECRET)")
            if not self.org_id:
                missing.append("org_id (env ANYPOINT_ORG_ID)")
        elif need == "llm":
            if not self.llm_proxy_url:
                missing.append("llm_proxy_url (env DONKEY_LLM_PROXY_URL)")
            if self.llm_proxy_auth == "jwt":
                # Model-wallet ingress (#509): NO client_secret — CIE is disabled
                # and the caller is identified from the JWT. The rotating JWT is
                # supplied via an AuthProvider (Donkey(llm_auth=...)), not config,
                # so it is not a "missing field" here — the provider-attached check
                # lives where the provider is known (LLMClient.client()). What IS
                # required here is the durable wallet-selector client ID.
                if not self.llm_proxy_wallet_client_id:
                    missing.append(
                        "llm_proxy_wallet_client_id (env DONKEY_LLM_PROXY_WALLET_CLIENT_ID) "
                        "— the wallet-selector X-Client-Id, required in jwt auth mode"
                    )
            else:
                # client-id enforcement (default): the LIVE-VERIFIED CIE pair.
                if not self.llm_proxy_client_id:
                    missing.append("llm_proxy_client_id (env DONKEY_LLM_PROXY_CLIENT_ID)")
                if not self.llm_proxy_client_secret:
                    missing.append(
                        "llm_proxy_client_secret (env DONKEY_LLM_PROXY_CLIENT_SECRET)"
                    )
        else:
            raise ConfigError(f"Unknown capability {need!r} passed to validated().")
        return missing

    def check_endpoints(self, *, need: str, code_credential: str | None = None) -> None:
        """Raise :class:`ConfigError` if ``need``'s endpoint may not receive the
        credentials that would be sent to it.

        The endpoint must be ``https://``; ``http://`` is accepted for loopback
        hosts, and for other hosts only with ``DONKEY_ALLOW_HTTP=1`` in the
        environment, which does not relax the rest of this check. And an
        endpoint read from the working directory's ``.donkey-kit.toml`` or its
        ``.local`` overlay only receives credentials read from those same files,
        unless it is a standard Anypoint control-plane host or
        ``DONKEY_TRUST_PROJECT_CONFIG=1`` is set in the environment. Loopback
        hosts are exempt from the ``https://`` rule only. In ``jwt``
        auth mode the JWT comes from the caller's ``AuthProvider``, never from
        those files, so it always counts as outside them. ``code_credential``
        names another credential supplied in code, such as the token of a
        ``Donkey(auth=...)`` provider, which counts as outside them too.
        """
        runtime_credential = code_credential
        if need == "control_plane":
            key, env_var, url = "base_url", "ANYPOINT_BASE_URL", self.control_plane_url
            credentials: tuple[str, ...] = ("client_id", "client_secret")
        elif need == "llm":
            if not self.llm_proxy_url:
                return
            key, env_var, url = "llm_proxy_url", "DONKEY_LLM_PROXY_URL", self.llm_proxy_url
            if self.llm_proxy_auth == "jwt":
                credentials = ("llm_proxy_wallet_client_id",)
                runtime_credential = "the JWT from the llm_auth provider"
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
        if _as_bool(os.environ.get(TRUST_PROJECT_CONFIG_ENV, "")):
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
                env_var=env_var,
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
        local = origin.path.parent / _LOCAL_TOML_NAME
        options.append(f"keep the credentials in {local}, next to the project file")
    options.append(
        f"trust this directory's config files by setting {TRUST_PROJECT_CONFIG_ENV}=1"
    )
    return ConfigError(
        f"Not sending {', '.join(credentials)} to {host_of(url)}: {key} is set in "
        f"{origin.path}, and credentials from outside the working directory's config "
        "files are only sent to hosts those files name when you opt in. To continue, "
        "do one of:\n  - " + "\n  - ".join(options)
    )


def _opt(v: object) -> str | None:
    return None if v is None else str(v)


# The four cost dimensions and the env var that overrides each, in field order.
_COST_ENV_VARS: tuple[tuple[str, str], ...] = (
    ("team", "DONKEY_COST_TEAM"),
    ("project", "DONKEY_COST_PROJECT"),
    ("env", "DONKEY_COST_ENV"),
    ("enduser_id", "DONKEY_COST_ENDUSER_ID"),
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
    env_over = {
        field: os.environ[var] for field, var in _COST_ENV_VARS if var in os.environ
    }
    return base.merge(CostTags(**env_over)) if env_over else base


def _as_int(v: object) -> int:
    return int(str(v))


def _as_float(v: object) -> float:
    return float(str(v))


def _as_bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _as_substitution(v: object) -> OnModelSubstitution:
    """Coerce and validate ``on_model_substitution`` (docs/verified-apis.md §3, #309).
    An unknown value is a config mistake worth reporting up front — a silent
    fall-back to ``"off"``
    would leave a caller who typed ``"error"`` believing they had opted into
    strictness. Validated at resolve time, like ``region``."""
    token = str(v).strip().lower()
    if token not in ("off", "raise"):
        raise ConfigError(
            f"Unknown on_model_substitution {v!r}. Expected 'off' or 'raise'."
        )
    return cast(OnModelSubstitution, token)


def _as_llm_proxy_auth(v: object) -> LlmProxyAuth:
    """Coerce and validate ``llm_proxy_auth`` (BG §1.1, #509). An unknown value is
    a config mistake worth reporting up front — a silent fall-back to
    ``"client-id"`` would leave a caller who typed ``"oauth"`` believing they had
    selected the wallet ingress. Validated at resolve time, like ``region`` and
    ``on_model_substitution``."""
    token = str(v).strip().lower()
    if token not in ("client-id", "jwt"):
        raise ConfigError(
            f"Unknown llm_proxy_auth {v!r}. Expected 'client-id' or 'jwt'."
        )
    return cast(LlmProxyAuth, token)


def _load_layers() -> list[tuple[ConfigSource, dict[str, object]]]:
    """The config file tables, highest precedence first.

    The working directory's ``.donkey-kit.local.toml`` over its
    ``.donkey-kit.toml``; if neither exists, ``$XDG_CONFIG_HOME/.donkey-kit.toml``.
    Missing files are fine; a malformed file raises."""

    cwd = Path.cwd()
    layers: list[tuple[ConfigSource, dict[str, object]]] = []
    local = cwd / _LOCAL_TOML_NAME
    if local.is_file():
        layers.append((ConfigSource("local", local), _read_table(local)))
    project = cwd / _TOML_NAME
    if project.is_file():
        table = _read_table(project)
        _warn_on_secrets(project, table)
        layers.append((ConfigSource("project", project), table))
    if layers:
        return layers

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        user = Path(xdg) / _TOML_NAME
        if user.is_file():
            return [(ConfigSource("user", user), _read_table(user))]
    return []


def _read_table(path: Path) -> dict[str, object]:
    """Read the ``[donkey]`` table, keeping only keys that are real config fields."""
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Malformed {path}: {exc}") from exc
    table = data.get("donkey", {})
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [donkey] must be a table.")
    known = {f.name for f in fields(DonkeyConfig) if not f.name.startswith("_")}
    return {k: v for k, v in table.items() if k in known}


def _warn_on_secrets(path: Path, table: dict[str, object]) -> None:
    found = [key for key in _SECRET_KEYS if key in table]
    if found:
        warnings.warn(
            f"{path} contains {', '.join(found)}. Keep secrets out of the committed "
            f"project file: move them to {_LOCAL_TOML_NAME} next to it (and gitignore "
            "it) or to environment variables.",
            ConfigWarning,
            stacklevel=4,
        )

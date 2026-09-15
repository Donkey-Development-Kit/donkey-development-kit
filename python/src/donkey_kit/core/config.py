"""Configuration (§2.1).

Resolution order: explicit kwarg → env var → ``.donkey-kit.toml`` (cwd or
``$XDG_CONFIG_HOME``) → default. We never read ``.env`` implicitly — the user
calls ``load_dotenv()`` themselves.

``validated()`` reports ALL missing fields in one error, not one per run — the
one-missing-variable-per-run loop is the most common first-five-minutes
abandonment (§2.1).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Literal, cast

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 has no stdlib tomllib; the [core] dep ``tomli`` backfills it.
    import tomli as tomllib

from ._verify import REGION_HOSTS
from .cost import CostTags
from .errors import ConfigError

Region = Literal["us", "eu", "ca", "jp"]

# What to do when the gateway serves a different model than the one requested —
# a routing fallback substituted the model (§3, #309). ``"off"`` (default): the
# substitution is surfaced passively on ``donkey.last_call.substituted`` and the
# span, but the call succeeds. ``"raise"``: the transport raises
# :class:`~donkey_kit.core.errors.ModelSubstituted`, for callers who need model
# determinism (e.g. an evaluation whose results are only comparable per-model).
OnModelSubstitution = Literal["off", "raise"]

_TOML_NAME = ".donkey-kit.toml"


@dataclass(frozen=True)
class DonkeyConfig:
    # --- Anypoint control plane (registry + provisioning) ---
    client_id: str | None = None          # env: ANYPOINT_CLIENT_ID
    client_secret: str | None = None      # env: ANYPOINT_CLIENT_SECRET
    org_id: str | None = None             # env: ANYPOINT_ORG_ID
    environment: str = "Sandbox"          # env: ANYPOINT_ENV
    region: Region = "us"                 # env: ANYPOINT_REGION
    base_url: str | None = None           # override; else derived from region

    # --- LLM proxy (data plane) — SEPARATE credential from the control plane ---
    # Auth is a client_id/client_secret REQUEST-header pair (client-id-enforcement),
    # LIVE-VERIFIED — docs/verified-apis.md §2/§3. NOT a bearer token.
    llm_proxy_url: str | None = None            # env: DONKEY_LLM_PROXY_URL
    llm_proxy_client_id: str | None = None      # env: DONKEY_LLM_PROXY_CLIENT_ID
    llm_proxy_client_secret: str | None = None  # env: DONKEY_LLM_PROXY_CLIENT_SECRET
    # Optional: fills the OpenAI SDK's mandatory ``api_key`` slot only. The proxy
    # authenticates on the client_id/secret headers above and ignores the bearer,
    # so this is rarely needed; leave unset to use a sentinel.
    llm_proxy_key: str | None = None            # env: DONKEY_LLM_PROXY_KEY

    # --- Attribution (real header names: see docs/verified-apis.md §3) ---
    application_name: str | None = None   # env: DONKEY_APP_NAME
    business_group: str | None = None     # env: DONKEY_BUSINESS_GROUP

    # --- Correlation request-header NAME overrides (§2.3, #195) ---
    # The gateway's inbound correlation/call-id header names are UNVERIFIED
    # (docs §3); these let a customer point them at the real names without a
    # release. Unset → the loud ``Unverified`` placeholders in ``core/_verify``.
    correlation_header: str | None = None  # env: DONKEY_CORRELATION_HEADER
    call_id_header: str | None = None      # env: DONKEY_CALL_ID_HEADER

    # --- Cost-attribution tags + request-header NAME overrides (§3, #196) ---
    # The fixed dimensions (team/project/env/enduser.id), set once and emitted on
    # every call. The gateway-side header names are the highest-priority unknown
    # (docs §3); the ``cost_*_header`` overrides let a customer point them at the
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
    # What to do when the gateway serves a different model than requested (§3,
    # #309). Default "off" — the substitution is surfaced passively on
    # ``donkey.last_call``; "raise" opts into a hard ``ModelSubstituted`` error.
    on_model_substitution: OnModelSubstitution = "off"

    # ----------------------------------------------------------------- factory
    @classmethod
    def from_env(cls) -> DonkeyConfig:
        """Build from env + optional ``.donkey-kit.toml``. Does not validate;
        call :meth:`validated` when you know which capability you need."""

        toml = _load_toml()

        def pick(env: str, key: str, default: object) -> object:
            if env in os.environ:
                return os.environ[env]
            if key in toml:
                return toml[key]
            return default

        region = str(pick("ANYPOINT_REGION", "region", "us"))
        if region not in REGION_HOSTS:
            raise ConfigError(
                f"Unknown region {region!r}. Expected one of {sorted(REGION_HOSTS)}."
            )

        return cls(
            cost=_resolve_cost_tags(toml.get("cost")),
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
        )

    # --------------------------------------------------------------- derived
    @property
    def control_plane_url(self) -> str:
        """The Anypoint control-plane base URL — explicit override or region."""
        return self.base_url or REGION_HOSTS[self.region]

    def with_overrides(self, **kw: object) -> DonkeyConfig:
        return replace(self, **kw)  # type: ignore[arg-type]

    # ------------------------------------------------------------- validation
    def validated(self, *, need: str = "control_plane") -> DonkeyConfig:
        """Return self if valid for the requested capability, else raise a
        :class:`ConfigError` listing EVERY missing field at once.

        ``need`` is one of ``"control_plane"`` (registry/provisioning) or
        ``"llm"`` (proxy). The two credentials are independent (§2.2): a user
        may legitimately have proxy access and no Exchange access.
        """

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
            if not self.llm_proxy_client_id:
                missing.append("llm_proxy_client_id (env DONKEY_LLM_PROXY_CLIENT_ID)")
            if not self.llm_proxy_client_secret:
                missing.append(
                    "llm_proxy_client_secret (env DONKEY_LLM_PROXY_CLIENT_SECRET)"
                )
        else:
            raise ConfigError(f"Unknown capability {need!r} passed to validated().")

        if missing:
            joined = "\n  - ".join(missing)
            raise ConfigError(
                f"Configuration for {need!r} is incomplete. Missing:\n  - {joined}\n"
                f"Set them via kwargs, environment variables, or {_TOML_NAME}."
            )
        return self


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
    dimension raises :class:`ConfigError`, never a silent drop, §196 AC #1), then
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
    """Coerce and validate ``on_model_substitution`` (§3, #309). An unknown value
    is a config mistake worth reporting up front — a silent fall-back to ``"off"``
    would leave a caller who typed ``"error"`` believing they had opted into
    strictness. Validated at resolve time, like ``region``."""
    token = str(v).strip().lower()
    if token not in ("off", "raise"):
        raise ConfigError(
            f"Unknown on_model_substitution {v!r}. Expected 'off' or 'raise'."
        )
    return cast(OnModelSubstitution, token)


def _load_toml() -> dict[str, object]:
    """Read ``[donkey]`` table from ``.donkey-kit.toml`` in cwd or
    ``$XDG_CONFIG_HOME``. Missing file is fine; malformed file raises."""

    candidates = [Path.cwd() / _TOML_NAME]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(Path(xdg) / _TOML_NAME)

    for path in candidates:
        if path.is_file():
            try:
                data = tomllib.loads(path.read_text())
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"Malformed {path}: {exc}") from exc
            table = data.get("donkey", {})
            if not isinstance(table, dict):
                raise ConfigError(f"{path}: [donkey] must be a table.")
            # Only accept keys that are real config fields.
            known = {f.name for f in fields(DonkeyConfig)}
            return {k: v for k, v in table.items() if k in known}
    return {}

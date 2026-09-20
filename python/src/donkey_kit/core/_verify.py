"""Centralized home for every value that the verification discipline says must
be verified against a real Anypoint sandbox before it can be trusted.

Working instruction #2: *never invent an endpoint, header name, or class name.*

Nothing in this module is a verified fact. Each value is either:

  * a ``PLACEHOLDER`` — a documented best-guess that is emitted with a loud,
    one-time :class:`UnverifiedValueWarning` whenever it is read, and is fully
    overridable via config / env so a customer can point it at the real value
    without waiting for us; or
  * absent, in which case the calling code raises
    ``NotImplementedError("blocked on verification: …")``.

When a value is confirmed against a sandbox, flip its row in
``docs/verified-apis.md`` to ``VERIFIED`` and set ``verified=True`` here so the
warning stops firing.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass


class UnverifiedValueWarning(UserWarning):
    """Emitted the first time an unverified placeholder constant is read."""


_warned: set[str] = set()


@dataclass(frozen=True)
class Unverified:
    """A placeholder value that is not yet confirmed against a real sandbox.

    Read it with :meth:`get`, which warns once. Treat the returned value as a
    default that the user can and should override.
    """

    key: str
    placeholder: str
    doc_ref: str
    verified: bool = False

    def get(self) -> str:
        if not self.verified and self.key not in _warned:
            _warned.add(self.key)
            warnings.warn(
                f"Using UNVERIFIED placeholder for {self.key!r} "
                f"({self.placeholder!r}). This has NOT been confirmed against a "
                f"real Anypoint sandbox — see {self.doc_ref}. Override it via "
                f"config/env, or verify it and flip the row in "
                f"docs/verified-apis.md.",
                UnverifiedValueWarning,
                stacklevel=2,
            )
        return self.placeholder


def blocked(what: str) -> NotImplementedError:
    """Construct the standard verification-blocked error.

    Use for surfaces where we have no defensible placeholder at all (e.g. the
    MCP Bridge provisioning endpoint, verification discipline / provisioning-as-code).
    """

    return NotImplementedError(f"blocked on verification: {what}")


# --- Attribution headers (docs/verified-apis.md §3, the single most important unknown) ---
# These names are GUESSES. The gateway may read entirely different header names.
ATTRIBUTION_APP_HEADER = Unverified(
    key="attribution.application_header",
    placeholder="X-Anypoint-Client-Application",
    doc_ref="docs/verified-apis.md §3",
)
ATTRIBUTION_BUSINESS_GROUP_HEADER = Unverified(
    key="attribution.business_group_header",
    placeholder="X-Anypoint-Business-Group",
    doc_ref="docs/verified-apis.md §3",
)

# --- Cost-attribution tag headers (docs/verified-apis.md §3, the HIGHEST-priority
# unknown, #196) ---
# The fixed cost dimensions (team / project / env / enduser.id) are emitted as
# request headers so the gateway can group spend per dimension. The gateway-side
# header NAMES are the single most important unverified value (docs/verified-apis.md §3): the
# direct-proxy path did NOT surface them, so these are loud, overridable
# placeholders (config: ``cost_*_header``), and the value ALWAYS lands on the
# ``donkey.cost.*`` span regardless (the SDK controls the span end to end, #196
# AC #4). The ``X-Anypoint-Cost-*`` shape mirrors the attribution placeholders
# above; it is a guess, not a confirmed name.
COST_TEAM_HEADER = Unverified(
    key="cost.team_header",
    placeholder="X-Anypoint-Cost-Team",
    doc_ref="docs/verified-apis.md §3",
)
COST_PROJECT_HEADER = Unverified(
    key="cost.project_header",
    placeholder="X-Anypoint-Cost-Project",
    doc_ref="docs/verified-apis.md §3",
)
COST_ENV_HEADER = Unverified(
    key="cost.env_header",
    placeholder="X-Anypoint-Cost-Env",
    doc_ref="docs/verified-apis.md §3",
)
COST_ENDUSER_HEADER = Unverified(
    key="cost.enduser_header",
    placeholder="X-Anypoint-Cost-Enduser-Id",
    doc_ref="docs/verified-apis.md §3",
)

# --- Correlation / call-id request headers (BG §1.1, #195) ------------------
# The gateway ECHOES `x-correlation-id` on RESPONSES (VERIFIED LIVE 2026-08-28,
# docs/verified-apis.md §3). Whether it READS an INBOUND correlation header —
# and under what name — is UNVERIFIED, as is any per-call request-id header. Both request-header
# names are therefore placeholders, overridable per-Donkey via config
# (``DonkeyConfig.correlation_header`` / ``.call_id_header``) so a customer can
# point them at the real names without waiting for us. ``X-Correlation-Id`` is
# the best guess precisely because it mirrors the verified response echo.
CORRELATION_ID_HEADER = Unverified(
    key="correlation.request_header",
    placeholder="X-Correlation-Id",
    doc_ref="docs/verified-apis.md §3",
)
CALL_ID_HEADER = Unverified(
    key="correlation.call_id_header",
    placeholder="X-Donkey-Request-Id",
    doc_ref="docs/verified-apis.md §3",
)

# --- Control-plane token endpoint (docs/verified-apis.md §1) ----------------
# Path is appended to the region base URL. VERIFIED (docs/verified-apis.md §12.1) from
# static analysis of the shipping `mulesoft-anypoint-cli-agent-fabric-plugin` (+ `anypoint-cli-
# command`): OAuth2 client_credentials → `POST /accounts/api/v2/oauth2/token`.
OAUTH_TOKEN_PATH = Unverified(
    key="anypoint.oauth_token_path",
    placeholder="/accounts/api/v2/oauth2/token",
    doc_ref="docs/verified-apis.md §12.1",
    verified=True,
)

# --- LLM proxy consumer auth (docs/verified-apis.md §2/§3) — VERIFIED (LIVE
# 2026-08-28) ------------
# The directly-called ingress LLM proxy authenticates the caller with a
# `client_id` + `client_secret` REQUEST-header pair (client-id-enforcement
# 1.3.3), NOT a bearer token. This pair IS the per-agent attribution unit. These
# are confirmed header names, not placeholders — see docs/verified-apis.md §2/§3.
LLM_PROXY_CLIENT_ID_HEADER = "client_id"
LLM_PROXY_CLIENT_SECRET_HEADER = "client_secret"

# --- Region host map (docs/verified-apis.md §1) ------------------------------
# UNVERIFIED — Hyperforce region hosts in particular need confirmation.
REGION_HOSTS: dict[str, str] = {
    "us": "https://anypoint.mulesoft.com",
    "eu": "https://eu1.anypoint.mulesoft.com",
    "ca": "https://ca1.anypoint.mulesoft.com",
    "jp": "https://jp1.anypoint.mulesoft.com",
}
REGION_HOSTS_VERIFIED = False

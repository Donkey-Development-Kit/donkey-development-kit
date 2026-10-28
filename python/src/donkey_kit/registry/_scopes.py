"""The connected-app scope each control-plane call needs (docs/scopes.md, #23).

Each scope was isolated with a fresh ``client_credentials`` app per candidate
set (docs/verified-apis.md §1): a scope removed from an app keeps working for a
while, so a reused app over-reports. A missing scope does not have one shape:
Accounts answers ``401``; API Manager, Exchange, Gateway Manager and API
Governance answer ``403``; the Exchange Graph search answers ``200`` with no
assets, so that one cannot be caught here.
"""

from __future__ import annotations

import httpx

from ..core.errors import AuthError

#: The consumer page listing every scope.
SCOPES_DOC = "https://docs.donkey-kit.dev/reference/scopes"

ENVIRONMENTS = "view:environment"
EXCHANGE_READ = "read:exchange"
EXCHANGE_PUBLISH = "manage:exchange"
API_READ = "read:api_configuration"
POLICIES_READ = "read:api_policies"
GATEWAY_READ = "read:servers"


def missing_scope(
    response: httpx.Response, message: str, *, scope: str, org_id: str | None
) -> AuthError:
    """The :class:`AuthError` for a ``401``/``403`` on a call that needs ``scope``."""
    where = f"business group {org_id}" if org_id else "the business group"
    return AuthError(
        message,
        remediation=(
            f"The connected app likely lacks the scope '{scope}' on {where}. Grant it to "
            f"the app in Access Management, then re-run. See {SCOPES_DOC}."
        ),
        response=response,
    )

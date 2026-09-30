"""Which request-header names a config key may set (config resolution).

``correlation_header``, ``call_id_header`` and the ``cost_*_header`` keys name
headers the SDK writes on every request. :func:`header_name_problem` says why a
name can't be used: it isn't a valid HTTP token, it routes or frames the
request, it carries a credential, or the SDK already sets it. Names compare
case-insensitively.
"""

from __future__ import annotations

import re

from . import _verify
from .masking import SENSITIVE_NAMES

# RFC 9110 field-name token.
_TOKEN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")

#: Headers that decide where a request goes or how its body is framed.
ROUTING_HEADERS: frozenset[str] = frozenset(
    {
        "host",
        "forwarded",
        "x-real-ip",
        "content-length",
        "transfer-encoding",
        "connection",
        "upgrade",
        "te",
        "trailer",
        "expect",
    }
)
ROUTING_PREFIXES: tuple[str, ...] = ("x-forwarded-",)

#: Headers that carry a credential or select one: every masked name plus the
#: identifiers the proxy authenticates or selects a wallet by.
CREDENTIAL_HEADERS: frozenset[str] = SENSITIVE_NAMES | {
    _verify.LLM_PROXY_CLIENT_ID_HEADER.lower(),
    _verify.LLM_PROXY_WALLET_CLIENT_ID_HEADER.lower(),
}

#: Other headers the SDK and its HTTP clients set on requests.
SDK_HEADERS: frozenset[str] = frozenset(
    name.lower()
    for name in (
        _verify.ATTRIBUTION_APP_HEADER.placeholder,
        _verify.ATTRIBUTION_BUSINESS_GROUP_HEADER.placeholder,
        _verify.CACHE_SKIP_HEADER.placeholder,
        _verify.CACHE_NO_STORE_HEADER.placeholder,
        _verify.CACHE_TTL_HEADER.placeholder,
        _verify.CACHE_THRESHOLD_HEADER.placeholder,
        _verify.CACHE_PRINCIPAL_ID_HEADER.placeholder,
        "content-type",
        "accept",
        "accept-encoding",
        "user-agent",
    )
)


def header_name_problem(name: str) -> str | None:
    """Why ``name`` can't be a configurable header name, or ``None`` if it can."""
    if not _TOKEN.fullmatch(name):
        return "it is not a valid HTTP header name"
    lowered = name.lower()
    if lowered in ROUTING_HEADERS or lowered.startswith(ROUTING_PREFIXES):
        return "it controls where the request goes or how it is framed"
    if lowered in CREDENTIAL_HEADERS:
        return "it carries or selects credentials"
    if lowered in SDK_HEADERS:
        return "the SDK already sets it"
    return None

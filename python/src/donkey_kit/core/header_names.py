"""Which request-header names a config key may set (config resolution).

``correlation_header``, ``call_id_header`` and the ``cost_*_header`` keys name
headers the SDK writes on every request. :func:`header_name_problem` says why a
name can't be used: it isn't a valid HTTP token, it routes or frames the
request, it can change the method or path, it carries a credential, the SDK or
a framework SDK already sets it, or it doesn't start with ``X-``. Names compare
case-insensitively.
"""

from __future__ import annotations

import re

from . import _verify
from .masking import SENSITIVE_NAMES

__all__ = [
    "CREDENTIAL_HEADERS",
    "FRAMEWORK_PREFIXES",
    "OVERRIDE_HEADERS",
    "ROUTING_HEADERS",
    "ROUTING_PREFIXES",
    "SDK_HEADERS",
    "header_name_problem",
]

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

#: Headers some servers and gateways read to change the method or the path.
OVERRIDE_HEADERS: frozenset[str] = frozenset(
    {
        "x-http-method-override",
        "x-http-method",
        "x-method-override",
        "x-original-url",
        "x-original-uri",
        "x-rewrite-url",
    }
)

#: Prefixes of headers the framework SDKs set on their own requests.
FRAMEWORK_PREFIXES: tuple[str, ...] = ("x-stainless-",)

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
        _verify.CACHE_SKIP_HEADER,
        _verify.CACHE_NO_STORE_HEADER,
        _verify.CACHE_TTL_HEADER,
        _verify.CACHE_THRESHOLD_HEADER,
        _verify.CACHE_PRINCIPAL_ID_HEADER,
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
    if lowered in OVERRIDE_HEADERS:
        return "it can change the request's method or path"
    if lowered in CREDENTIAL_HEADERS:
        return "it carries or selects credentials"
    if lowered in SDK_HEADERS or lowered.startswith(FRAMEWORK_PREFIXES):
        return "the SDK already sets it"
    if not lowered.startswith("x-"):
        return "custom header names must start with X-"
    return None

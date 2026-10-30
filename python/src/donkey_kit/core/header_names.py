"""Which request-header names a config key may set (config resolution).

``correlation_header``, ``call_id_header`` and the ``cost_*_header`` keys name
headers the SDK writes on every request. :func:`header_name_problem` says why a
name can't be used: it isn't a valid HTTP token, it routes or frames the
request, it can change the method or path, it carries a credential, the SDK or
a framework SDK already sets it, or it doesn't start with ``X-``. Names compare
case-insensitively.

``llm_proxy_key_header`` names the header the provider key rides in (#367), so
:func:`key_header_problem` lets it be a credential header such as ``x-api-key``
and drops the ``X-`` rule. It still refuses the headers that carry the
consumer's own credentials.

``user_token_header`` names the header the ``donkey.as_user()`` token rides in
(BG §2.4, #215), so :func:`user_token_header_problem` allows ``Authorization``
and refuses the other consumer-credential headers.
"""

from __future__ import annotations

import re

from . import _verify
from .masking import SENSITIVE_NAMES

__all__ = [
    "CONSUMER_AUTH_HEADERS",
    "CREDENTIAL_HEADERS",
    "FRAMEWORK_PREFIXES",
    "OVERRIDE_HEADERS",
    "ROUTING_HEADERS",
    "ROUTING_PREFIXES",
    "SDK_HEADERS",
    "header_name_problem",
    "key_header_problem",
    "user_token_header_problem",
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

#: Headers that carry the consumer's own credentials, which a provider key must
#: not ride in: the ones the SDK authenticates or selects a wallet with, the
#: proxy-auth and cookie headers, and ``apikey``, which the proxy reads as the
#: packed ``client_id:client_secret`` consumer credential (#510).
CONSUMER_AUTH_HEADERS: frozenset[str] = frozenset(
    name.lower()
    for name in (
        _verify.LLM_PROXY_CLIENT_ID_HEADER,
        _verify.LLM_PROXY_CLIENT_SECRET_HEADER,
        _verify.LLM_PROXY_WALLET_CLIENT_ID_HEADER,
        _verify.LLM_PROXY_WALLET_JWT_HEADER,
        "proxy-authorization",
        "cookie",
        "apikey",
    )
)

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


def _unsafe(name: str) -> str | None:
    """The problems every configurable header name is checked for."""
    if not _TOKEN.fullmatch(name):
        return "it is not a valid HTTP header name"
    lowered = name.lower()
    if lowered in ROUTING_HEADERS or lowered.startswith(ROUTING_PREFIXES):
        return "it controls where the request goes or how it is framed"
    if lowered in OVERRIDE_HEADERS:
        return "it can change the request's method or path"
    return None


def _sdk_set(lowered: str) -> bool:
    return lowered in SDK_HEADERS or lowered.startswith(FRAMEWORK_PREFIXES)


def header_name_problem(name: str) -> str | None:
    """Why ``name`` can't be a configurable header name, or ``None`` if it can."""
    if (problem := _unsafe(name)) is not None:
        return problem
    lowered = name.lower()
    if lowered in CREDENTIAL_HEADERS:
        return "it carries or selects credentials"
    if _sdk_set(lowered):
        return "the SDK already sets it"
    if not lowered.startswith("x-"):
        return "custom header names must start with X-"
    return None


def key_header_problem(name: str) -> str | None:
    """Why ``name`` can't carry the provider key (``llm_proxy_key_header``,
    #367), or ``None`` if it can. Any header the route's Dynamic key expression
    reads is allowed, ``x-api-key`` included, except one that routes or frames
    the request or that the SDK already sets."""
    if (problem := _unsafe(name)) is not None:
        return problem
    lowered = name.lower()
    if lowered in CONSUMER_AUTH_HEADERS:
        return "it carries the consumer's own credentials"
    if _sdk_set(lowered):
        return "the SDK already sets it"
    return None


def user_token_header_problem(name: str) -> str | None:
    """Why ``name`` can't carry the ``donkey.as_user()`` token, which the SDK
    forwards unchanged (``user_token_header``, BG §2.4), or ``None`` if it can.
    ``Authorization`` is allowed; any other header that carries the consumer's
    own credentials, routes or frames the request, or that the SDK already sets
    is not."""
    if (problem := _unsafe(name)) is not None:
        return problem
    lowered = name.lower()
    if lowered == _verify.USER_TOKEN_HEADER.lower():
        return None
    if lowered in CONSUMER_AUTH_HEADERS:
        return "it carries the consumer's own credentials"
    if _sdk_set(lowered):
        return "the SDK already sets it"
    return None

"""Endpoint checks shared by config validation and the token request (config resolution).

Credentials only travel over ``https://``. Plain ``http://`` is accepted for
loopback hosts (``localhost``, ``127.0.0.0/8``, ``::1``) because the local
gateway simulator and local development listen there, and for any other host
only when ``DONKEY_ALLOW_HTTP=1`` is set in the environment. A URL that carries
a user name or password is refused whatever the scheme or host (#1478).

:func:`url_credential_problem` is the check for a URL about to be *shown or
written* (a generated config file, a public declaration): it also refuses
credential-looking query or fragment parameters, and a URL that cannot be parsed,
since nothing about such a value proves it carries no credential (#1746).
"""

from __future__ import annotations

import ipaddress
import os
import warnings
from urllib.parse import SplitResult, parse_qsl, urlsplit

from ._verify import REGION_HOSTS
from .errors import ConfigError, ConfigWarning

__all__ = [
    "ALLOW_HTTP_ENV",
    "STANDARD_CONTROL_PLANE_HOSTS",
    "allow_http_enabled",
    "allow_http_setting",
    "host_of",
    "is_loopback",
    "is_loopback_host",
    "looks_like_credential_param",
    "require_secure_url",
    "url_credential_problem",
]

#: The Anypoint control-plane hosts the SDK already knows, one per region.
STANDARD_CONTROL_PLANE_HOSTS: frozenset[str] = frozenset(
    host for url in REGION_HOSTS.values() if (host := urlsplit(url).hostname)
)

#: Env-only switch that allows plain ``http://`` to non-loopback hosts.
ALLOW_HTTP_ENV = "DONKEY_ALLOW_HTTP"


def allow_http_enabled() -> bool:
    """Whether ``DONKEY_ALLOW_HTTP`` is on. Read from the environment only."""
    return os.environ.get(ALLOW_HTTP_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def allow_http_setting() -> str:
    """The switch as set, e.g. ``DONKEY_ALLOW_HTTP=true``, for messages."""
    return f"{ALLOW_HTTP_ENV}={os.environ.get(ALLOW_HTTP_ENV, '').strip()}"


def _split(url: str) -> SplitResult | None:
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - validates the port; raises ValueError when malformed
    except ValueError:
        return None
    return parts


def host_of(url: str) -> str | None:
    """The lower-cased host of ``url``, or ``None`` when it has none."""
    parts = _split(url)
    return parts.hostname if parts else None


def is_loopback_host(host: str) -> bool:
    """True when ``host`` is ``localhost`` or a loopback IP address (``127.0.0.0/8``,
    ``::1``). ``0.0.0.0`` is not: it binds every interface."""
    host = host.strip().strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_loopback(url: str) -> bool:
    """True when ``url``'s host is ``localhost`` or a loopback IP address."""
    host = host_of(url)
    return host is not None and is_loopback_host(host)


def require_secure_url(url: str, *, name: str) -> None:
    """Raise :class:`ConfigError` unless ``url`` is ``https://`` with a host, or
    ``http://`` to a loopback host. ``http://`` to any other host passes with a
    :class:`ConfigWarning` when ``DONKEY_ALLOW_HTTP`` is on (``1``, ``true``,
    ``yes`` or ``on``) in the environment. A URL with a user name or password is
    refused whatever ``DONKEY_ALLOW_HTTP`` says: httpx would send them to the host
    as ``Authorization: Basic``."""
    parts = _split(url)
    if parts is not None and (parts.username is not None or parts.password is not None):
        # Never echo the URL: it carries a credential.
        raise ConfigError(
            f"{name} carries a user name or password (host {parts.hostname or 'no host'}).",
            remediation=(
                "Remove the user name and password from the URL; the SDK sends the "
                "credentials itself."
            ),
        )
    if parts is not None and parts.hostname:
        if parts.scheme == "https":
            return
        if parts.scheme == "http" and is_loopback(url):
            return
        if parts.scheme == "http" and allow_http_enabled():
            warnings.warn(
                f"{name} uses plain http:// to {parts.hostname} because "
                f"{allow_http_setting()} is set. Credentials and data sent to it are "
                "not encrypted in transit.",
                ConfigWarning,
                stacklevel=2,
            )
            return
    scheme = parts.scheme if parts is not None and parts.scheme else "no"
    host = (parts.hostname if parts is not None else None) or "no host"
    remedy = "Change it to the https:// address of the service"
    if scheme == "http" and parts is not None and parts.hostname:
        remedy += (
            f", or set {ALLOW_HTTP_ENV}=1 in the environment to allow plain http:// to other hosts"
        )
    raise ConfigError(
        f"{name} must be an https:// URL (got {scheme} scheme, {host}). Plain http:// "
        "is accepted for loopback hosts (localhost, 127.0.0.0/8, ::1), such as the "
        f"local gateway simulator. {remedy}."
    )


# Query parameter names that carry a credential, compared lower-cased with "-"
# and "_" removed: an exact name, or a fragment anywhere in the name.
_CREDENTIAL_PARAMS = frozenset({"auth", "key", "pwd", "sig"})
_CREDENTIAL_FRAGMENTS = (
    "accesskey",
    "apikey",
    "authorization",
    "clientid",
    "credential",
    "passwd",
    "password",
    "secret",
    "signature",
    "token",
)


def looks_like_credential_param(name: str) -> bool:
    """True when a URL query parameter called ``name`` looks like it carries a
    credential (``api_key``, ``access-token``, ``sig``, ...)."""
    key = name.lower().replace("-", "").replace("_", "")
    return key in _CREDENTIAL_PARAMS or any(f in key for f in _CREDENTIAL_FRAGMENTS)


def url_credential_problem(url: str) -> str | None:
    """Why ``url`` must not be shown or written verbatim, or ``None`` when it may.

    A URL is refused when it carries a user name or password, when a query or
    fragment parameter looks like a credential, or when it cannot be parsed at
    all (a malformed port, say), since then nothing proves it is credential-free.
    The reason never contains the URL or any part of its value, so it is safe to
    print.
    """
    parts = _split(url)
    if parts is None:
        return "is not a valid URL (it is not shown, in case it carries a credential)"
    if parts.username is not None or parts.password is not None:
        return "carries a user name or password"
    for where, text in (("query", parts.query), ("fragment", parts.fragment)):
        for name, _ in parse_qsl(text, keep_blank_values=True):
            if looks_like_credential_param(name):
                return f"has a {where} parameter {name!r} that looks like a credential"
    return None

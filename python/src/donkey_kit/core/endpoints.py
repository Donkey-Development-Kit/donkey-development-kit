"""Endpoint checks shared by config validation and the token request (config resolution).

Credentials only travel over ``https://``. Plain ``http://`` is accepted for
loopback hosts (``localhost``, ``127.0.0.0/8``, ``::1``) because the local
gateway simulator and local development listen there, and for any other host
only when ``DONKEY_ALLOW_HTTP=1`` is set in the environment.
"""

from __future__ import annotations

import ipaddress
import os
import warnings
from urllib.parse import SplitResult, urlsplit

from ._verify import REGION_HOSTS
from .errors import ConfigError, ConfigWarning

#: The Anypoint control-plane hosts the SDK already knows, one per region.
STANDARD_CONTROL_PLANE_HOSTS: frozenset[str] = frozenset(
    host for url in REGION_HOSTS.values() if (host := urlsplit(url).hostname)
)

#: Env-only switch that allows plain ``http://`` to non-loopback hosts.
ALLOW_HTTP_ENV = "DONKEY_ALLOW_HTTP"


def allow_http_enabled() -> bool:
    """Whether ``DONKEY_ALLOW_HTTP`` is on. Read from the environment only."""
    return os.environ.get(ALLOW_HTTP_ENV, "").strip().lower() in ("1", "true", "yes", "on")


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


def is_loopback(url: str) -> bool:
    host = host_of(url)
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_secure_url(url: str, *, name: str) -> None:
    """Raise :class:`ConfigError` unless ``url`` is ``https://`` with a host, or
    ``http://`` to a loopback host. ``http://`` to any other host passes with a
    :class:`ConfigWarning` when ``DONKEY_ALLOW_HTTP=1`` is set in the environment."""
    parts = _split(url)
    if parts is not None and parts.hostname:
        if parts.scheme == "https":
            return
        if parts.scheme == "http" and is_loopback(url):
            return
        if parts.scheme == "http" and allow_http_enabled():
            warnings.warn(
                f"{name} uses plain http:// to {parts.hostname} because "
                f"{ALLOW_HTTP_ENV}=1 is set. Credentials and data sent to it are "
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
            f", or set {ALLOW_HTTP_ENV}=1 in the environment to allow plain http:// "
            "to other hosts"
        )
    raise ConfigError(
        f"{name} must be an https:// URL (got {scheme} scheme, {host}). Plain http:// "
        "is accepted only for loopback hosts (localhost, 127.0.0.0/8, ::1), such as "
        f"the local gateway simulator. {remedy}."
    )

"""Endpoint checks shared by config validation and the token request (config resolution).

Credentials only travel over ``https://``. Plain ``http://`` is accepted for
loopback hosts (``localhost``, ``127.0.0.0/8``, ``::1``) because the local
gateway simulator and local development listen there.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import SplitResult, urlsplit

from ._verify import REGION_HOSTS
from .errors import ConfigError

#: The Anypoint control-plane hosts the SDK already knows, one per region.
STANDARD_CONTROL_PLANE_HOSTS: frozenset[str] = frozenset(
    host for url in REGION_HOSTS.values() if (host := urlsplit(url).hostname)
)


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
    ``http://`` to a loopback host."""
    parts = _split(url)
    if parts is not None and parts.hostname:
        if parts.scheme == "https":
            return
        if parts.scheme == "http" and is_loopback(url):
            return
    scheme = parts.scheme if parts is not None and parts.scheme else "no"
    host = (parts.hostname if parts is not None else None) or "no host"
    raise ConfigError(
        f"{name} must be an https:// URL (got {scheme} scheme, {host}). Plain http:// "
        "is accepted only for loopback hosts (localhost, 127.0.0.0/8, ::1), such as "
        "the local gateway simulator. Change it to the https:// address of the service."
    )

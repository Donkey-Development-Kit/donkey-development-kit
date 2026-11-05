"""Framework-free version matching for A2A serving and capture selection."""

from __future__ import annotations

import re

__all__ = ["matches_protocol_version"]

# a2a-sdk 1.2.0/1.3.0 utils.version_validator parses packaging.version.Version
# then compares major only. Match its PEP 440 grammar without adding a base
# dependency or importing the optional SDK into the simulator (BG section 2.9.3).
_VERSION = re.compile(
    r"\s*v?(?:[0-9]+!)?(?P<major>[0-9]+)(?:\.[0-9]+)*"
    r"(?:[._-]?(?:alpha|a|beta|b|preview|pre|c|rc)[._-]?[0-9]*)?"
    r"(?:-[0-9]+|[._-]?(?:post|rev|r)[._-]?[0-9]*)?"
    r"(?:[._-]?dev[._-]?[0-9]*)?"
    r"(?:\+[a-z0-9]+(?:[._-][a-z0-9]+)*)?\s*",
    re.IGNORECASE | re.ASCII,
)


def matches_protocol_version(actual: str, expected: str) -> bool:
    """Match an explicit header using the official SDK's major-version rule.

    Malformed versions never match. Empty/missing-header defaults belong to
    the caller, since ingress preserves its historical headerless replay.
    ``expected`` is a selected protocol (``1.0`` or ``0.3.0``).
    """
    match = _VERSION.fullmatch(actual.strip())
    return match is not None and (match["major"].lstrip("0") or "0") == expected.split(".")[0]

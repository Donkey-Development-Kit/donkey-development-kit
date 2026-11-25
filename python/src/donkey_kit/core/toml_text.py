"""Writing TOML text by hand: keys, basic strings and comments (#1747).

The standard library reads TOML (``tomllib``) but does not write it, and the
SDK adds no writer dependency. What it generates is small (``donkey init``'s
connection table, ``donkey scaffold``'s declaration tables), so these three
helpers render it, and callers check the result by parsing it back.
"""

from __future__ import annotations

import re

__all__ = ["basic_string", "comment", "key"]

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
# TOML's short escapes; any other control character is written as \\uXXXX.
_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _is_control(char: str) -> bool:
    return char < " " or char == "\x7f"


def basic_string(value: str) -> str:
    """``value`` as a TOML basic string, quotes included.

    Raises:
        ValueError: ``value`` holds a lone surrogate, which no TOML string
            (and no UTF-8 file) can carry.
    """
    out = []
    for char in value:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif _is_control(char):
            out.append(f"\\u{ord(char):04X}")
        elif "\ud800" <= char <= "\udfff":
            raise ValueError(f"{value!r} holds a lone surrogate, which TOML cannot carry")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def key(value: str) -> str:
    """``value`` as a TOML key: bare when it may be, else a quoted basic string."""
    return value if _BARE_KEY.fullmatch(value) else basic_string(value)


def comment(text: str) -> str:
    """``text`` made safe to follow ``#``: a TOML comment may hold no control
    character but tab, and a UTF-8 file no lone surrogate, so both are escaped
    as text (``\\u000A``, ``\\udcff``) rather than dropped."""
    safe = text.encode("utf-8", "backslashreplace").decode("utf-8")
    return "".join(f"\\u{ord(c):04X}" if _is_control(c) and c != "\t" else c for c in safe)

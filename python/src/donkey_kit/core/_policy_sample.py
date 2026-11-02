"""Regex leaf examples for custom-policy registrations (BG §2.2)."""

from __future__ import annotations

import re
import string

__all__ = ["sample"]

_ALPHABET = string.ascii_letters + string.digits + " _-.:/" + string.printable


def sample(pattern: re.Pattern[str]) -> str:
    """A value the pattern fullmatches, or ValueError for unsupported patterns."""
    source = pattern.pattern
    try:
        value, _end = _sample_seq(source, 0, pattern.flags)
    except (re.error, IndexError, ValueError):
        value = None
    if value is None or pattern.fullmatch(value) is None:
        raise ValueError(
            f"Cannot synthesise a value that {source!r} fullmatches, which the "
            "registration checks and donkey.simulate() need. Use a simpler pattern."
        )
    return value


def _sample_seq(src: str, i: int, flags: int) -> tuple[str, int]:
    parts: list[str] = []
    while i < len(src) and src[i] != ")":
        if src[i] == "|":
            depth = 0
            while i < len(src) and not (src[i] == ")" and depth == 0):
                depth += {"(": 1, ")": -1}.get(src[i], 0)
                i += 2 if src[i] == "\\" else 1
            break
        atom, i = _sample_atom(src, i, flags)
        count, i = _quantifier(src, i)
        parts.append(atom * count)
    return "".join(parts), i


def _sample_atom(src: str, i: int, flags: int) -> tuple[str, int]:
    char = src[i]
    if char == "(":
        if src.startswith("(?P<", i):
            i = src.index(">", i) + 1
        elif src.startswith("(?:", i):
            i += 3
        elif src.startswith("(?", i):
            return "", _close(src, i) + 1
        else:
            i += 1
        inner, i = _sample_seq(src, i, flags)
        return inner, i + 1
    if char in "^$":
        return "", i + 1
    if char == "[":
        end = src.index("]", i + 2 if src.startswith("[^", i) else i + 1)
        if src[end - 1] == "\\":
            end = src.index("]", end + 1)
        atom = src[i : end + 1]
        return _one_char(atom, flags), end + 1
    atom = src[i : i + 2] if char == "\\" else char
    return _one_char(atom, flags), i + len(atom)


def _one_char(atom: str, flags: int) -> str:
    compiled = re.compile(atom, flags)
    return next((c for c in _ALPHABET if compiled.fullmatch(c)), "")


def _quantifier(src: str, i: int) -> tuple[int, int]:
    count = 1
    if i < len(src) and src[i] in "*?+":
        count = 0 if src[i] in "*?" else 1
        i += 1
    else:
        repeat = re.match(r"\{(\d*)(?:,\d*)?\}", src[i:])
        if repeat is None:
            return count, i
        count = int(repeat.group(1) or 0)
        i += repeat.end()
    if i < len(src) and src[i] in "?+":
        i += 1
    return count, i


def _close(src: str, i: int) -> int:
    depth = 0
    while True:
        depth += {"(": 1, ")": -1}.get(src[i], 0)
        if depth == 0:
            return i
        i += 2 if src[i] == "\\" else 1

"""Schema-valid JSON for a structured-output request (BG §3.4, #231).

The simulator's Chat Completions happy path is a captured 200 whose message is
fixed prose, so ``client.chat.completions.parse(response_format=Model)`` cannot
parse it. When a request asks for ``json_schema`` output, the app serves the same
captured envelope with ``choices[0].message.content`` replaced by a JSON document
built here from the schema. That content is synthesised: no gateway capture of a
structured 200 exists yet, so the envelope is the only real part of the response.
It is produced only for requests that ask for structured output.

Honoured: ``type`` (a single type or a list), ``properties`` (every property is
emitted, so strict-mode objects validate), ``items`` with ``minItems``, ``enum``,
``const``, ``anyOf``/``oneOf`` (first non-null branch), local ``$ref`` into
``$defs``/``definitions``, ``minLength``, ``minimum``/``exclusiveMinimum``, and
the ``date``, ``date-time``, ``time``, ``uuid``, ``email`` and ``uri`` formats.
Not satisfied: ``pattern``, ``multipleOf``, ``maxLength`` and the other
constraints. A schema that needs them can get a document a strict validator
rejects. A schema the builder cannot resolve (an unknown or non-local ``$ref``,
or recursion deeper than ``_MAX_DEPTH``) yields ``None``, and the plain happy
path is served instead.
"""

from __future__ import annotations

import json
import math
from typing import Any

__all__ = ["structured_content"]

_MAX_DEPTH = 32

# One valid example per format the builder knows; any other format is served as
# a plain string of ``minLength`` characters.
_FORMATS: dict[str, str] = {
    "date": "1970-01-01",
    "date-time": "1970-01-01T00:00:00Z",
    "time": "00:00:00",
    "uuid": "00000000-0000-4000-8000-000000000000",
    "email": "user@example.com",
    "uri": "https://example.com/",
}


class _Unbuildable(Exception):
    """The schema has a shape this builder cannot satisfy (see module docstring)."""


def structured_content(response_format: object) -> str | None:
    """The JSON document a ``json_schema`` ``response_format`` asks for, as text,
    or ``None`` when the request does not ask for structured output or its schema
    cannot be built."""
    if not isinstance(response_format, dict) or response_format.get("type") != "json_schema":
        return None
    spec = response_format.get("json_schema")
    schema = spec.get("schema") if isinstance(spec, dict) else None
    if not isinstance(schema, dict):
        return None
    try:
        value = _example(schema, schema, 0)
    except _Unbuildable:
        return None
    return json.dumps(value, separators=(",", ":"))


def _example(node: object, root: dict[str, Any], depth: int) -> object:
    """A value that validates against ``node`` (within the module's limits)."""
    if depth > _MAX_DEPTH:
        raise _Unbuildable("schema recursion deeper than the simulator's limit")
    if not isinstance(node, dict):
        return None
    if "$ref" in node:
        return _example(_resolve(node["$ref"], root), root, depth + 1)
    if "const" in node:
        return node["const"]
    enum = node.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    for key in ("anyOf", "oneOf"):
        branches = node.get(key)
        if isinstance(branches, list) and branches:
            chosen = next((b for b in branches if not _is_null(b)), branches[0])
            return _example(chosen, root, depth + 1)

    kind = node.get("type")
    if isinstance(kind, list):
        kind = next((t for t in kind if t != "null"), "null")
    if kind == "object":
        props = node.get("properties")
        if not isinstance(props, dict):
            return {}
        return {name: _example(sub, root, depth + 1) for name, sub in props.items()}
    if kind == "array":
        count = _number(node.get("minItems"), 0)
        items = node.get("items")
        return [_example(items, root, depth + 1) for _ in range(int(count))]
    if kind == "string":
        fmt = node.get("format")
        if isinstance(fmt, str) and fmt in _FORMATS:
            return _FORMATS[fmt]
        return "x" * int(_number(node.get("minLength"), 0))
    if kind in ("integer", "number"):
        exclusive = _optional_number(node.get("exclusiveMinimum"))
        if kind == "integer":
            if exclusive is not None:
                return math.floor(exclusive) + 1
            return math.ceil(_number(node.get("minimum"), 0))
        if exclusive is not None:
            return exclusive + 1
        return _number(node.get("minimum"), 0)
    if kind == "boolean":
        return False
    return None  # "null", an unknown type, or a schema with no type (any value)


def _resolve(ref: object, root: dict[str, Any]) -> dict[str, Any]:
    """The schema a local JSON pointer (``#/$defs/Name``) names in ``root``."""
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise _Unbuildable(f"non-local $ref {ref!r}")
    target: Any = root
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or key not in target:
            raise _Unbuildable(f"unresolvable $ref {ref!r}")
        target = target[key]
    if not isinstance(target, dict):
        raise _Unbuildable(f"$ref {ref!r} does not name a schema")
    return target


def _is_null(node: object) -> bool:
    return isinstance(node, dict) and node.get("type") == "null"


def _optional_number(value: object) -> float | None:
    """``value`` as a float when it is a JSON number (a bool is not one), else ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _number(value: object, default: float) -> float:
    number = _optional_number(value)
    return default if number is None else number

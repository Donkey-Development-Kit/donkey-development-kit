"""Reading a refusal response's body for ``core.errors.classify()``: the JSON
envelope shapes the gateway and providers send, and the PII-detection policy's
rejection message (its flagged entity types and offsets, and a summary that
withholds the flagged values). docs/verified-apis.md §4."""

from __future__ import annotations

import json
import re
from typing import Any

from ._response import ResponseLike

__all__ = [
    "PiiSpan",
    "code_str",
    "json_body",
    "nested_error",
    "pii_spans",
    "pii_summary",
    "str_or_none",
]

_PII_TYPE_RE = re.compile(r'"pii_type"\s*:\s*"([^"]+)"')

#: One flagged PII entity: ``(entity type, start offset, end offset)``.
PiiSpan = tuple[str, int | None, int | None]


def pii_spans(message: str | None) -> list[PiiSpan]:
    """Best-effort ``(entity type, start, end)`` for each entity the PII policy
    flagged, parsed from its rejection message (a JSON list of ``{"pii_type",
    "value", "start", "end"}`` objects; docs/verified-apis.md §4). The ``value``
    is never read. Offsets are ``None`` when the list does not parse; an empty
    list means no ``pii_type`` markers were found.

    Deliberately best-effort (#289): the LLM PII Detection policy documents only
    the ``{"error":{"message","type":"pii_detected"}}`` envelope, not a structured
    entity field — the ``pii_type`` markers are observed in the live-captured
    message string, so ``PIIDetected.entities`` is populated from the message and
    is ``[]`` when the message carries no such markers, never invented. Reconcile
    against a documented entity field if one lands (#253)."""
    if not message:
        return []
    bracket = message.find("[")
    if bracket != -1:
        try:
            parsed = json.loads(message[bracket:])
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            spans = [
                (item["pii_type"], _int_or_none(item.get("start")), _int_or_none(item.get("end")))
                for item in parsed
                if isinstance(item, dict) and isinstance(item.get("pii_type"), str)
            ]
            if spans:
                return spans
    return [(entity, None, None) for entity in _PII_TYPE_RE.findall(message)]


def pii_summary(status: int, spans: list[PiiSpan]) -> str:
    """The :class:`~donkey_kit.core.errors.PIIDetected` message: the entity types, their count and
    offsets — never the flagged values the gateway echoes back."""
    base = f"Request blocked: personally identifiable information detected ({status})"
    if not spans:
        return f"{base}."
    parts = [
        entity if start is None or end is None else f"{entity} at chars {start}-{end}"
        for entity, start, end in spans
    ]
    noun = "entity" if len(spans) == 1 else "entities"
    return (
        f"{base}: {len(spans)} {noun} ({', '.join(parts)}). "
        "Values withheld; the gateway's text is on .gateway_message."
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_json(response: ResponseLike) -> object:
    """The response's parsed JSON body (of any shape — object, list, scalar), or
    ``None`` when the body is absent or not JSON. Never raises on the caller's
    request path (verification discipline). An unread streamed body
    (``ResponseNotRead``) is "absent" too: the transport reads a streamed refusal
    before classifying it (#805), so this only guards a direct caller. Caught as
    ``RuntimeError``, its base on both HTTP stacks (#933)."""
    try:
        return response.json()
    except (ValueError, UnicodeDecodeError, RuntimeError):
        return None


def json_body(response: ResponseLike) -> dict[str, Any] | None:
    """The response's top-level JSON *object*, or ``None`` when the body is
    absent, not JSON, or not an object. Fail-open: a list-shaped body (e.g.
    Gemini's error envelope, #548) returns ``None`` here — its nested error is
    recovered separately by :func:`nested_error`."""
    body = _parse_json(response)
    return body if isinstance(body, dict) else None


def nested_error(response: ResponseLike) -> dict[str, Any] | None:
    """The provider's nested ``error`` object, or ``None``.

    Two envelope shapes are seen live (docs/verified-apis.md §4):

    * an OBJECT envelope ``{"error": {...}}`` — OpenAI-format proxies and
      gateway LLM policies (e.g. PII);
    * a LIST envelope ``[{"error": {...}}]`` — Gemini's native error shape,
      whose first element is the object envelope (#548).

    Fail-open (BG §1.5): any other shape — a bare list, a list whose first
    element carries no nested ``error`` object, a scalar — returns ``None`` so an
    unrecognised body still falls through to the honest generic refusal rather
    than a guess."""
    parsed = _parse_json(response)
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed and isinstance(parsed[0], dict) else None
    if not isinstance(parsed, dict):
        return None
    error_obj = parsed.get("error")
    return error_obj if isinstance(error_obj, dict) else None


def code_str(value: object) -> str | None:
    """The provider's error ``code`` as a string. OpenAI sends a string
    (``model_not_found``); Gemini sends a number (400, #548). Both are carried;
    any other type (``bool`` included) is dropped."""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(value)
    return None


def str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None

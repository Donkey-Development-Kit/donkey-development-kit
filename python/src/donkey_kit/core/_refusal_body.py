"""Reading a refusal response for ``core.errors.classify()``: the JSON envelope
shapes the gateway and providers send, the PII-detection policy's rejection
message (its flagged entity types and offsets, and a summary that withholds the
flagged values), the rate-limit and content-safety headers, and the ids the
client sent. docs/verified-apis.md §4."""

from __future__ import annotations

import json
import re
from typing import Any

from . import _verify, _wire
from ._response import ResponseLike

__all__ = [
    "MODEL_NOT_ROUTABLE_RE",
    "PiiSpan",
    "code_str",
    "content_safety_reject",
    "json_body",
    "nested_error",
    "parse_retry_after",
    "pii_spans",
    "pii_summary",
    "retry_after",
    "sent_ids",
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


def sent_ids(response: ResponseLike) -> tuple[str | None, str | None]:
    """The ``(correlation_id, call_id)`` the client sent, read back from the
    response's own request headers under the names the transport actually used
    (BG §1.1, #195, #363).

    The transport stamps the resolved header names onto ``request.extensions``
    at injection time (``donkey_correlation_header`` / ``donkey_call_id_header``),
    so a per-``Donkey`` header-name override (``DonkeyConfig.correlation_header``
    / ``.call_id_header``, docs/verified-apis.md §3) is honoured here without ``core/_refusal_body``
    importing ``DonkeyConfig`` — it only reads a plain dict carried on the same
    object ``response.request`` returns. When the stamp is absent (a response not
    produced by our transport — e.g. a hand-built stock-client response), the
    default names from ``core/_verify`` are used.

    Returns ``(None, None)`` when the request is unavailable (httpx raises if it
    was never set on the response)."""
    try:
        request = response.request
    except RuntimeError:
        return None, None
    headers = request.headers
    corr_name = request.extensions.get("donkey_correlation_header") or _verify.CORRELATION_ID_HEADER
    call_name = request.extensions.get("donkey_call_id_header") or _verify.CALL_ID_HEADER
    return headers.get(corr_name), headers.get(call_name)


def retry_after(
    response: ResponseLike, *, reset_header: str = _wire.TOKEN_RESET_HEADER
) -> float | None:
    """Seconds until the caller may retry. Prefers the standard ``retry-after``
    (delta-seconds) header; falls back to the rate-limit policy's reset header
    (``x-token-reset``, or ``x-ratelimit-reset`` for the request window), which
    is captured in **milliseconds** (docs/verified-apis.md §4)."""
    seconds = parse_retry_after(response.headers.get(_wire.RETRY_AFTER_HEADER))
    if seconds is not None:
        return seconds
    reset_ms = response.headers.get(reset_header)
    if reset_ms is not None:
        try:
            return max(0.0, float(reset_ms) / 1000.0)
        except ValueError:
            return None
    return None


def parse_retry_after(raw: str | None) -> float | None:
    """A ``Retry-After`` delta-seconds value, floored at 0, or ``None`` when it
    is absent or not a number. The one parser behind both the transport's retry
    sleep and an error's ``retry_after``: a negative value (e.g. ``"-1"``) means
    "retry now", never a negative wait (#286, #815)."""
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None  # HTTP-date form; left for the fixture-driven parser (BG §1.5)


# The model-based-routing rejection for a bare model name on a multi-provider
# proxy (docs/verified-apis.md §4, #825). Live text: "Failed to parse model from
# request: Model 'gpt-5-mini' is not in the known unique model map and multiple
# providers are configured. Use 'provider/model' format."
MODEL_NOT_ROUTABLE_RE = re.compile(r"Model '(?P<model>[^']*)' is not in the known unique model map")

# Content-safety / guardrails policies report their verdict in a pair of vendor
# headers — an ``...-action`` (``allow``|``reject``) and a comma-separated
# ``...-reason``. Both are ``x-llm-proxy-<vendor>-...`` (docs/verified-apis.md §4,
# #253/#568/#1178). Each policy also sends ``action: reject`` on its fail-closed
# ``503`` (the moderation service was unreachable), so only a ``403`` is a block.
# A vendor joins only once its shape is captured live (§0.3): CrowdStrike AIDR waits
# on #1184 and falls through to the unconfirmed refusal until then (#1429).
_CONTENT_SAFETY_VENDORS: tuple[tuple[str, str, str, str], ...] = (
    (
        _wire.AZURE_CONTENT_SAFETY_ACTION_HEADER,
        _wire.AZURE_CONTENT_SAFETY_REASON_HEADER,
        "azure-content-safety",
        "Azure Content Safety",
    ),
    (
        _wire.BEDROCK_GUARDRAIL_ACTION_HEADER,
        _wire.BEDROCK_GUARDRAIL_REASON_HEADER,
        "bedrock-guardrails",
        "Amazon Bedrock Guardrails",
    ),
    (
        _wire.MODEL_ARMOR_ACTION_HEADER,
        _wire.MODEL_ARMOR_REASON_HEADER,
        "google-model-armor",
        "Google Model Armor",
    ),
)


def content_safety_reject(response: ResponseLike) -> tuple[str, str, list[str]] | None:
    """``(provider, vendor, categories)`` when a content-safety policy header reports
    ``action: reject`` on a ``403``, else ``None`` (the same header on the
    fail-closed ``503`` is an outage, #1178). ``provider`` is the stable slug that
    ``ContentSafetyBlocked.provider`` and ``donkey.policy.provider`` carry (#1183);
    ``vendor`` is the display name. Categories are the comma-separated flagged
    reasons from the sibling ``...-reason`` header (empty list if absent)."""
    if response.status_code != 403:
        return None
    for action_h, reason_h, provider, vendor in _CONTENT_SAFETY_VENDORS:
        if response.headers.get(action_h) == "reject":
            reason = response.headers.get(reason_h, "")
            categories = [c.strip() for c in reason.split(",") if c.strip()]
            return provider, vendor, categories
    return None

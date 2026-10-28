"""The ``match`` spec behind ``classify.register()`` (BG §2.2, #210): its
validation, the minimal response it describes, and whether it holds on a parsed
refusal. ``core/classifier`` owns the registry and the public object; this
module is the part that needs no registry state.
"""

from __future__ import annotations

import re
import string
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, TypeAlias

import httpx

from ._refusal_body import code_str
from .errors import (
    _POLICY_MATCHERS,
    AgentKilled,
    ContentSafetyBlocked,
    DonkeyError,
    PIIDetected,
    PolicyViolation,
    PromptInjectionBlocked,
    RequestRateLimitExceeded,
    TokenBudgetExceeded,
    _Shape,
    _shape,
)
from .step_up import GatewayApprovalDenied

__all__ = ["POLICY_SLUGS", "BodyValue", "PolicyRegistration", "condition_key", "holds", "validated"]

#: A ``body`` leaf: compared by type-strict equality, or ``fullmatch`` for a
#: compiled pattern (string leaves only).
BodyValue: TypeAlias = str | int | bool | re.Pattern[str]

# --- donkey.policy.type (#192, BG §1.6) -------------------------------------
# Most-specific subclass first: a PIIDetected is also a PolicyViolation.
POLICY_SLUGS: tuple[tuple[type[DonkeyError], str], ...] = (
    (TokenBudgetExceeded, "token_budget"),
    (RequestRateLimitExceeded, "request_rate_limit"),
    (PIIDetected, "pii_detected"),
    (PromptInjectionBlocked, "injection"),
    (ContentSafetyBlocked, "content_safety"),
    (AgentKilled, "agent_killed"),
    (GatewayApprovalDenied, "approval_denied"),
    (PolicyViolation, "policy_violation"),
)

#: Slugs a registration may not use: every ``donkey.policy.type`` value above
#: and every built-in ``.policy`` value (``"regex-prompt-guard"`` is set per
#: instance by the Regex Prompt Guard rule), so neither field is ever ambiguous.
_RESERVED_SLUGS = frozenset(
    {slug for _kind, slug in POLICY_SLUGS}
    | {str(getattr(kind, "policy", "")) for kind, _slug in POLICY_SLUGS}
    | {"regex-prompt-guard"}
)
_SLUG_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}")

#: Upstream provider error values captured in docs/verified-apis.md §4 (the
#: nested ``type``, Gemini's ``status``, and ``code``). A spec discriminated by
#: these alone would claim real provider mistakes. Pinned against the captured
#: fixtures by tests/unit/test_classifier.py.
_PROVIDER_VALUES = frozenset(
    {
        "",
        "400",
        "404",
        "INVALID_ARGUMENT",
        "NOT_FOUND",
        "decimal_above_max_value",
        "invalid_request_error",
        "model_not_found",
        "upstream_error",
    }
)
_PROVIDER_PATHS = frozenset({"error.type", "error.status", "error.code"})
_MATCH_KEYS = frozenset({"status", "error_type", "error_code", "headers", "body"})


@dataclass(frozen=True)
class PolicyRegistration:
    """One ``classify.register()`` entry, as :meth:`Classifier.registered`
    returns it. Every given condition must hold on a response with ``status``.

    ``headers`` are ``(lowercased name, exact value)`` pairs and ``body`` is
    ``(dotted path, leaf)`` pairs over the top-level JSON object, both sorted.
    ``policy`` is the slug the raised exception and its span carry."""

    raises: type[PolicyViolation]
    policy: str
    status: int
    error_type: str | None = None
    error_code: str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    body: tuple[tuple[str, BodyValue], ...] = ()

    @property
    def conditions(self) -> int:
        """How many discriminators the spec names (``status`` excluded): the
        specificity that settles two registrations matching one response."""
        named = (self.error_type is not None) + (self.error_code is not None)
        return named + len(self.headers) + len(self.body)

    def example(self) -> httpx.Response:
        """The minimal response this spec describes: ``status``, its headers,
        and a JSON body built from ``error_type`` / ``error_code`` / the body
        paths (a pattern leaf gets a sample value it fullmatches). The
        registration checks run it through the built-in rules, and
        ``donkey.simulate()`` serves it.

        Raises:
            ValueError: No response satisfies every condition (a contradictory
                spec, e.g. ``body={"error": "x"}`` with ``error_type``), or no
                sample value fullmatches a pattern leaf.
        """
        headers = dict(self.headers)
        body = _example_body(self)
        if body is None:
            response = httpx.Response(self.status, headers=headers)
        else:
            response = httpx.Response(self.status, headers=headers, json=body)
        if not holds(self, _shape(response, correlation_id=None, call_id=None)):
            raise ValueError(
                f"Contradictory match spec for {self.raises.__name__}: no response "
                "can satisfy every condition (e.g. a body path and error_type both "
                "claim 'error')."
            )
        return response


def condition_key(reg: PolicyRegistration) -> tuple[object, ...]:
    """What makes two registrations duplicates: the same conditions, whatever
    they raise. 1 and True compare equal in Python; the leaf's type keeps them
    apart."""
    body = tuple((path, type(leaf).__name__, leaf) for path, leaf in reg.body)
    return (reg.status, reg.error_type, reg.error_code, reg.headers, body)


# --- register() validation --------------------------------------------------


def validated(match: object, raises: object) -> PolicyRegistration:
    """The :class:`PolicyRegistration` ``match`` and ``raises`` describe, after
    every check that needs no registry state (duplicates and slug reuse are the
    registry's to check)."""
    if not isinstance(match, Mapping):
        raise TypeError(f"match must be a mapping, got {type(match).__name__}.")
    unknown = sorted(str(key) for key in match if key not in _MATCH_KEYS)
    if unknown:
        raise TypeError(
            f"Unknown match key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(_MATCH_KEYS))}."
        )
    if "status" not in match:
        raise TypeError("match requires a status (a 4xx other than 401 and 429).")
    status = match["status"]
    if type(status) is not int:
        raise TypeError(f"match status must be an int, got {status!r}.")
    if not 400 <= status < 500 or status in (401, 429):
        raise ValueError(
            f"match status {status} is not registrable: use a 4xx other than 401 "
            "(always auth) and 429 (always a rate limit or token budget)."
        )
    error_type = _optional_str(match, "error_type")
    error_code = _optional_str(match, "error_code")
    headers = _headers(match.get("headers"))
    body = _body(match.get("body"))
    if error_type is None and error_code is None and not headers and not body:
        raise ValueError(
            f"match=dict(status={status}) has no discriminator, so it would catch "
            f"every {status}; add error_type, error_code, headers or body."
        )
    kind = _registrable(raises)
    spec = PolicyRegistration(kind, "", status, error_type, error_code, headers, body)
    if _provider_values_only(spec):
        raise ValueError(
            "This spec is discriminated only by upstream provider error values "
            "captured in docs/verified-apis.md §4, so it would turn real provider "
            "mistakes into policy refusals. Match on a value your policy sets."
        )
    _unshadowed(spec)
    policy = _slug(kind, error_type, error_code)
    _probe(kind, policy)
    return replace(spec, policy=policy)


def _unshadowed(spec: PolicyRegistration) -> None:
    """Run the spec's own example through the built-in rules: one that claims it
    would always win, so the registration could never match. Derived from the
    rules themselves, so it stays right as built-ins are added."""
    shape = _shape(spec.example(), correlation_id=None, call_id=None)
    for matcher in _POLICY_MATCHERS:
        claimed = matcher(shape)
        if claimed is not None:
            raise ValueError(
                f"The built-in {matcher.__name__.removeprefix('_match_')} rule claims "
                f"this spec (it raises {type(claimed).__name__}); a verified shape "
                f"always wins, so the registration for {spec.raises.__name__} could "
                "never match."
            )


def _optional_str(match: Mapping[str, object], key: str) -> str | None:
    value = match.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise TypeError(f"match {key} must be a non-empty str, got {value!r}.")
    return value


def _headers(value: object) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise TypeError(f"match headers must be a mapping, got {type(value).__name__}.")
    pairs: dict[str, str] = {}
    for name, header_value in value.items():
        if not (isinstance(name, str) and name and isinstance(header_value, str)):
            raise TypeError(f"match headers must map str to str, got {name!r}: {header_value!r}.")
        if name.lower() in pairs:
            raise ValueError(f"match headers name {name!r} twice (names are case-insensitive).")
        pairs[name.lower()] = header_value
    return tuple(sorted(pairs.items()))


def _body(value: object) -> tuple[tuple[str, BodyValue], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise TypeError(f"match body must be a mapping, got {type(value).__name__}.")
    leaves: list[tuple[str, BodyValue]] = []
    for path, leaf in value.items():
        if not isinstance(path, str) or not all(path.split(".")):
            raise ValueError(f"match body path {path!r} must be a dotted path of non-empty keys.")
        pattern = isinstance(leaf, re.Pattern) and isinstance(leaf.pattern, str)
        if not (pattern or type(leaf) in (str, int, bool)):
            raise TypeError(
                f"match body[{path!r}] must be a str, int, bool or compiled str "
                f"pattern, got {leaf!r}."
            )
        leaves.append((path, leaf))
    return tuple(sorted(leaves, key=lambda pair: pair[0]))


def _registrable(raises: object) -> type[PolicyViolation]:
    if not (isinstance(raises, type) and issubclass(raises, PolicyViolation)):
        raise TypeError(f"raises must be a PolicyViolation subclass, got {raises!r}.")
    if raises is PolicyViolation:
        raise TypeError("raises must be your own PolicyViolation subclass, not PolicyViolation.")
    own = raises.__mro__[: raises.__mro__.index(PolicyViolation)]
    home = PolicyViolation.__module__
    builtin = next((k for k in own if k.__module__ == home and issubclass(k, DonkeyError)), None)
    if builtin is not None:
        raise TypeError(
            f"{raises.__name__} subclasses the built-in {builtin.__name__}; built-in "
            "types are not extensible (AgentKilled is terminal). Subclass "
            "PolicyViolation instead."
        )
    remediation = next((k.__dict__["remediation"] for k in own if "remediation" in vars(k)), None)
    if not isinstance(remediation, str) or not remediation.strip():
        raise TypeError(
            f"{raises.__name__} must define its own non-empty remediation naming "
            "the caller's next step (BG §1.2); the PolicyViolation default does not count."
        )
    return raises


def _slug(kind: type[PolicyViolation], error_type: str | None, error_code: str | None) -> str:
    own = kind.__mro__[: kind.__mro__.index(PolicyViolation)]
    policy = next((k.__dict__["policy"] for k in own if "policy" in k.__dict__), None)
    slug = policy if policy is not None else (error_type or error_code)
    if slug is None:
        raise ValueError(
            f"{kind.__name__} sets no policy, and a header- or body-only match has "
            "no natural slug: set policy = '<slug>' on the class."
        )
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        raise ValueError(
            f"Slug {slug!r} is not a valid policy name (lowercase letter, then up to "
            f"63 of a-z 0-9 _ -): set a valid policy on {kind.__name__}."
        )
    if slug in _RESERVED_SLUGS:
        raise ValueError(
            f"Slug {slug!r} is a built-in policy name, so donkey.policy.type would "
            f"be ambiguous: set a distinct policy on {kind.__name__}."
        )
    return slug


def _provider_values_only(reg: PolicyRegistration) -> bool:
    if reg.headers or any(path not in _PROVIDER_PATHS for path, _leaf in reg.body):
        return False
    values: list[BodyValue] = [v for v in (reg.error_type, reg.error_code) if v is not None]
    values += [leaf for _path, leaf in reg.body]
    return all(_provider_value(v) for v in values)


def _provider_value(value: BodyValue) -> bool:
    if isinstance(value, re.Pattern):
        return any(value.fullmatch(known) for known in _PROVIDER_VALUES)
    return not isinstance(value, bool) and str(value) in _PROVIDER_VALUES


# The shared kwargs classify() passes every classified error (``_Shape.kw``).
_NO_IDS: dict[str, None] = dict.fromkeys(("correlation_id", "call_id", "request_id", "response"))


def _probe(kind: type[PolicyViolation], policy: str) -> None:
    """Build one instance the way a refusal will, so an incompatible
    ``__init__`` fails at registration, not on the first refusal."""
    try:
        probe = kind("probe", policy=policy, **_NO_IDS)
    except Exception as exc:
        raise TypeError(
            f"{kind.__name__} cannot be built as {kind.__name__}(message, policy=..., "
            f"**kw), which classify() needs: {type(exc).__name__}: {exc}"
        ) from exc
    if probe.policy != policy:
        raise TypeError(f"{kind.__name__}(message, policy={policy!r}) must set .policy to it.")


# --- the synthesised example ------------------------------------------------


def _example_body(reg: PolicyRegistration) -> dict[str, Any] | None:
    if not reg.body and reg.error_type is None and reg.error_code is None:
        return None
    body: dict[str, Any] = {}
    for path, leaf in reg.body:
        _put(body, path.split("."), _sample(leaf) if isinstance(leaf, re.Pattern) else leaf)
    if reg.error_type is not None:
        _put(body, ["error", "type"], reg.error_type)
    if reg.error_code is not None:
        _put(body, ["error", "code"], reg.error_code)
    return body


def _put(body: dict[str, Any], segments: list[str], value: object) -> None:
    """Set a dotted path in the example body. A key already set is kept: the
    self-match check in :meth:`PolicyRegistration.example` then rejects a spec
    whose two conditions disagree about it."""
    node = body
    for segment in segments[:-1]:
        child = node.setdefault(segment, {})
        if not isinstance(child, dict):
            raise ValueError(
                f"Contradictory match spec: {'.'.join(segments)!r} needs an object "
                f"at {segment!r}, which another condition sets to {child!r}."
            )
        node = child
    node.setdefault(segments[-1], value)


# Characters tried, in order, for one single-character atom of a pattern.
_ALPHABET = string.ascii_letters + string.digits + " _-.:/" + string.printable


def _sample(pattern: re.Pattern[str]) -> str:
    """A value ``pattern`` fullmatches, for the example body. A small heuristic
    over the pattern source (literals, escapes, classes, groups, the first
    alternative, each quantifier's minimum), confirmed by ``fullmatch``."""
    source = pattern.pattern
    try:
        sample, _end = _sample_seq(source, 0, pattern.flags)
    except (re.error, IndexError, ValueError):
        sample = None
    if sample is None or pattern.fullmatch(sample) is None:
        raise ValueError(
            f"Cannot synthesise a value that {source!r} fullmatches, which the "
            "registration checks and donkey.simulate() need. Use a simpler pattern."
        )
    return sample


def _sample_seq(src: str, i: int, flags: int) -> tuple[str, int]:
    """The sample for the first alternative of the sequence at ``src[i]``, and
    the index of the ``)`` (or end) that closes it."""
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
        elif src.startswith("(?", i):  # a flag group or lookaround: zero-width
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
    """The first character of :data:`_ALPHABET` that ``atom`` fullmatches, or
    ``""`` for a zero-width escape (``\\b``, ``\\A``)."""
    compiled = re.compile(atom, flags)
    return next((c for c in _ALPHABET if compiled.fullmatch(c)), "")


def _quantifier(src: str, i: int) -> tuple[int, int]:
    """The minimum repeat count of the quantifier at ``src[i]`` (1 when there is
    none), and the index after it and any lazy/possessive suffix."""
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


# --- classify-time matching -------------------------------------------------

_MISSING = object()


def holds(reg: PolicyRegistration, s: _Shape) -> bool:
    """Every condition of ``reg`` holds on the parsed response ``s``."""
    if reg.status != s.status:
        return False
    if reg.error_type is not None and s.error_type != reg.error_type:
        return False
    if reg.error_code is not None:
        code = code_str(s.error_obj.get("code")) if s.error_obj is not None else None
        if code != reg.error_code:
            return False
    if reg.headers:
        lowered = {name.lower(): value for name, value in s.response.headers.items()}
        if any(lowered.get(name) != value for name, value in reg.headers):
            return False
    return all(_leaf_holds(expected, _leaf(s.body, path)) for path, expected in reg.body)


def _leaf(body: dict[str, Any] | None, path: str) -> object:
    node: object = body
    for segment in path.split("."):
        if not isinstance(node, dict) or segment not in node:
            return _MISSING
        node = node[segment]
    return node


def _leaf_holds(expected: BodyValue, actual: object) -> bool:
    if isinstance(expected, re.Pattern):
        return isinstance(actual, str) and expected.fullmatch(actual) is not None
    return type(actual) is type(expected) and actual == expected

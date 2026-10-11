"""``--scenario`` scripting for the local gateway simulator (BG §1.4, #188).

The simulator is only useful if a developer can provoke a *specific* failure on
demand. A scenario is a small, stateful fault-injection rule applied to every
model call the simulator serves (``POST /responses`` and
``POST /chat/completions``, #895):

- ``pii_block:every=N`` — every Nth call is served the ``pii-detected`` 403.
- ``injection:on-pattern=<substr>`` — a call whose request text contains
  ``<substr>`` (case-insensitive) is served the ``injection-protection`` 400.
- ``budget:limit=<tokens>,window=<dur>[,cost=<tokens>]`` — a real, wall-clock
  windowed token counter. Each call deducts ``cost`` tokens (default: the
  happy-path fixture's own reported ``total_tokens``); once the window's budget
  is spent, calls are served the ``token-rate-limit`` **429** — with
  ``x-token-remaining``/``x-token-reset``/``x-token-limit`` recomputed from the
  live counter and the real milliseconds left in the window — until the window
  rolls over and the budget resets.
- ``request_limit:limit=<requests>,window=<dur>`` — a wall-clock windowed
  request counter (#974). Each passing ``200`` carries the gateway's
  ``x-ratelimit-limit`` / ``x-ratelimit-remaining`` / ``x-ratelimit-reset``
  trio, as the stock ``rate-limiting`` policy sends it with ``exposeHeaders:
  true``; once ``limit`` calls are spent, calls are served the captured
  ``request-rate-limit`` **429** with the trio recomputed, until the window
  rolls over. The live window is aligned to the gateway's clock; this one
  starts on the first call.

**Verification discipline (#352/#353/#354).** The live happy-path ``200``
carries the budget window as the prose ``x-llm-proxy-ratelimit`` header, *not*
the numeric ``x-token-*`` trio (those appear only on the ``429``). So the
``budget`` scenario emits the prose header on the ``200`` (rendered from its live
counter) and the numeric ``x-token-*`` only on the ``429`` boundary. It never
fabricates a header shape the gateway does not emit.

Framework isolation (the layered architecture): this module imports only the stdlib and the
framework-free sibling :mod:`~donkey_kit.simulator.fixtures` — never a web
framework — so ``import donkey_kit.simulator.scenarios`` stays green under the
base-only CI job.

Scenarios are **stateful and single-use**: one parsed scenario set drives one
simulator instance. :func:`parse_scenarios` constructs fresh instances, so a
second ``build_app`` call needs a freshly parsed set.
"""

from __future__ import annotations

import json
import logging
import time
import warnings
from typing import Protocol, runtime_checkable

from .fixtures import (
    LIMIT_HEADER,
    RATELIMIT_HEADER,
    REMAINING_HEADER,
    REQUEST_LIMIT_HEADER,
    REQUEST_REMAINING_HEADER,
    REQUEST_RESET_HEADER,
    RESET_HEADER,
    load,
    render_ratelimit_prose,
)

__all__ = [
    "BudgetScenario",
    "FaultScenario",
    "InjectionScenario",
    "PiiBlockScenario",
    "RequestLimitScenario",
    "ScenarioError",
    "ScenarioHit",
    "parse_scenario",
    "parse_scenarios",
    "request_text",
]

_log = logging.getLogger(__name__)

# Fixed shape names (owned by the fixtures table) each scenario serves. Named
# here rather than inlined so a shape rename fails loudly at import against the
# SHAPES table via load().
_PII_SHAPE = "pii-detected"
_INJECTION_SHAPE = "injection-protection"
_BUDGET_SHAPE = "token-rate-limit"
_REQUEST_LIMIT_SHAPE = "request-rate-limit"

# Fallback per-call cost if the success fixture carries no usage total — a
# defensible non-zero default so the counter still moves (see _default_cost).
_FALLBACK_COST = 68


class ScenarioError(ValueError):
    """A malformed ``--scenario`` spec. The CLI turns this into an exit-2 usage
    error rather than a stack trace."""


class ScenarioHit:
    """A scenario's decision to serve a specific fixture shape, plus any header
    overrides layered on top of that fixture's replayed headers (extra wins)."""

    __slots__ = ("shape", "extra_headers")

    def __init__(self, shape: str, extra_headers: dict[str, str] | None = None) -> None:
        self.shape = shape
        self.extra_headers = extra_headers or {}


@runtime_checkable
class FaultScenario(Protocol):
    """A stateful fault-injection rule evaluated once per model call
    (``POST /responses`` or ``POST /chat/completions``).
    Named ``Scenario`` before #719, which collided with the conformance kit's
    :class:`donkey_kit.conformance.Scenario`."""

    name: str

    def on_call(self, text: str) -> ScenarioHit | None:
        """Return a :class:`ScenarioHit` to reject this call, or ``None`` to pass.
        ``text`` is the request's human-readable content (see :func:`request_text`)."""
        ...


def _texts(node: object) -> list[str]:
    """Recursively collect the human-readable strings out of an ``input`` /
    ``messages`` / ``content`` node (str, list, or ``{role, content}`` /
    ``{type, text}`` dict), so injection matching sees what a human wrote."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        out: list[str] = []
        for item in node:
            out.extend(_texts(item))
        return out
    if isinstance(node, dict):
        out = []
        for key in ("content", "text", "input"):
            if key in node:
                out.extend(_texts(node[key]))
        return out
    return []


def request_text(payload: object) -> str:
    """Extract the searchable request text from a ``/responses`` (``input`` /
    ``instructions``) or Chat-Completions (``messages``) payload. Returns ``""``
    for a non-dict or textless body."""
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    instructions = payload.get("instructions")
    if isinstance(instructions, str):
        parts.append(instructions)
    parts.extend(_texts(payload.get("input")))
    parts.extend(_texts(payload.get("messages")))
    return "\n".join(parts)


class InjectionScenario:
    """Serve the ``injection-protection`` 400 when the request text contains a
    substring (case-insensitive). Stateless."""

    name = "injection"

    def __init__(self, pattern: str) -> None:
        if not pattern:
            raise ScenarioError("injection: on-pattern must be a non-empty string")
        self._pattern = pattern.lower()

    def on_call(self, text: str) -> ScenarioHit | None:
        """Reject with the prompt-injection shape when ``text`` contains the pattern."""
        if self._pattern in text.lower():
            return ScenarioHit(_INJECTION_SHAPE)
        return None


class PiiBlockScenario:
    """Serve the ``pii-detected`` 403 on every Nth call, deterministically."""

    name = "pii_block"

    def __init__(self, every: int) -> None:
        if every < 1:
            raise ScenarioError(f"pii_block: every must be >= 1, got {every}")
        self._every = every
        self._count = 0

    def on_call(self, text: str) -> ScenarioHit | None:  # noqa: ARG002 - Scenario protocol
        """Reject with the PII shape on every Nth call."""
        self._count += 1
        if self._count % self._every == 0:
            return ScenarioHit(_PII_SHAPE)
        return None


def _default_cost() -> int:
    """Per-call token cost, defaulting to the happy-path fixture's own reported
    ``total_tokens`` — an honest number, not a fabricated one (verification discipline)."""
    try:
        body = json.loads(load("success").body)
        total = int(body.get("usage", {}).get("total_tokens", 0))
        return total if total > 0 else _FALLBACK_COST
    except Exception:  # noqa: BLE001 — any parse trouble falls back to the constant
        # A packaged fixture that will not parse is a bug, not a user condition.
        _log.debug("success fixture unreadable; using the fallback cost", exc_info=True)
        return _FALLBACK_COST


class BudgetScenario:
    """A real, wall-clock-windowed token counter.

    Each call deducts ``cost`` tokens. While budget remains in the current
    window, the call passes and the happy-path ``200`` carries the prose
    ``x-llm-proxy-ratelimit`` header rendered from the live counter. Once the
    window's budget is spent, calls are served the ``token-rate-limit`` 429 with
    ``x-token-*`` recomputed from the counter and the real ms left in the window,
    until the window rolls over and the budget resets to ``limit``.

    Not shared across simulator instances; holds a mutable counter and window
    clock. ``on_call`` runs start-to-finish without awaiting, so it is atomic
    against the simulator's concurrent requests on a single event loop.
    """

    name = "budget"

    def __init__(self, limit: int, window_ms: int, cost: int | None = None) -> None:
        if limit < 1:
            raise ScenarioError(f"budget: limit must be >= 1, got {limit}")
        if window_ms < 1:
            raise ScenarioError(f"budget: window must be >= 1ms, got {window_ms}ms")
        self._limit = limit
        self._window_ms = window_ms
        self._cost = cost if cost is not None else _default_cost()
        if self._cost < 1:
            raise ScenarioError(f"budget: cost must be >= 1, got {self._cost}")
        self._remaining = limit
        self._window_start: float | None = None
        self._reset_ms = window_ms  # last computed ms-to-reset, for the prose header

    def _advance_window(self) -> None:
        """Start the window on the first call; roll it over (resetting the budget)
        once the wall clock has passed ``window_ms``. Records ms-to-reset."""
        now = time.monotonic()
        if self._window_start is None:
            self._window_start = now
        elapsed_ms = (now - self._window_start) * 1000.0
        if elapsed_ms >= self._window_ms:
            self._window_start = now
            self._remaining = self._limit
            elapsed_ms = 0.0
        self._reset_ms = max(0, int(self._window_ms - elapsed_ms))

    def on_call(self, text: str) -> ScenarioHit | None:  # noqa: ARG002 - Scenario protocol
        """Spend this call's tokens; reject with the token-budget shape once the window is spent."""
        self._advance_window()
        if self._remaining <= 0:
            return ScenarioHit(
                _BUDGET_SHAPE,
                {
                    LIMIT_HEADER: str(self._limit),
                    REMAINING_HEADER: "0",
                    RESET_HEADER: str(self._reset_ms),
                },
            )
        self._remaining = max(0, self._remaining - self._cost)
        return None

    def happy_path_headers(self) -> dict[str, str]:
        """The prose ``x-llm-proxy-ratelimit`` header for a passing call, rendered
        from the live counter. Call only after :meth:`on_call` returned ``None``."""
        return {
            RATELIMIT_HEADER: render_ratelimit_prose(self._remaining, self._limit, self._reset_ms)
        }


class RequestLimitScenario:
    """A wall-clock-windowed request counter (#974).

    Each passing call spends one request, and its ``200`` carries the
    ``x-ratelimit-*`` trio from the live counter. Once ``limit`` requests are
    spent, calls are served the ``request-rate-limit`` 429 with the trio
    recomputed, until the window rolls over and the count resets to ``limit``.
    Like :class:`BudgetScenario`, single-use and atomic per call.

    The window is fixed, as on the live gateway (docs/verified-apis.md §4): it
    starts when the scenario is built, not on the first call, and rolls over
    every ``window_ms`` whether or not calls arrive. The gateway's own alignment
    is not captured, so the simulator does not imitate it.
    """

    name = "request_limit"

    def __init__(self, limit: int, window_ms: int) -> None:
        if limit < 1:
            raise ScenarioError(f"request_limit: limit must be >= 1, got {limit}")
        if window_ms < 1:
            raise ScenarioError(f"request_limit: window must be >= 1ms, got {window_ms}ms")
        self._limit = limit
        self._window_ms = window_ms
        self._remaining = limit
        self._window_start = time.monotonic()
        self._reset_ms = window_ms

    def _advance_window(self) -> None:
        """Roll the fixed window over by whole windows once ``window_ms`` has
        passed, and record the ms left."""
        elapsed_ms = (time.monotonic() - self._window_start) * 1000.0
        if elapsed_ms >= self._window_ms:
            windows = int(elapsed_ms // self._window_ms)
            self._window_start += windows * self._window_ms / 1000.0
            self._remaining = self._limit
            elapsed_ms -= windows * self._window_ms
        self._reset_ms = max(0, int(self._window_ms - elapsed_ms))

    def _headers(self) -> dict[str, str]:
        return {
            REQUEST_LIMIT_HEADER: str(self._limit),
            REQUEST_REMAINING_HEADER: str(self._remaining),
            REQUEST_RESET_HEADER: str(self._reset_ms),
        }

    def on_call(self, text: str) -> ScenarioHit | None:  # noqa: ARG002 - Scenario protocol
        """Spend one request; reject with the request-rate-limit shape once the window is spent."""
        self._advance_window()
        if self._remaining <= 0:
            return ScenarioHit(_REQUEST_LIMIT_SHAPE, self._headers())
        self._remaining -= 1
        return None

    def happy_path_headers(self) -> dict[str, str]:
        """The ``x-ratelimit-*`` trio for a passing call, from the live counter.
        Call only after :meth:`on_call` returned ``None``."""
        return self._headers()


_DURATION_UNITS_MS = {"ms": 1, "s": 1000, "m": 60_000}


def _parse_duration_ms(raw: str, scenario: str) -> int:
    """Parse a duration like ``60s``, ``500ms``, ``2m`` into milliseconds. A bare
    number is read as seconds (``window=60`` == ``60s``)."""
    raw = raw.strip()
    for unit in ("ms", "s", "m"):  # "ms" before "s" so it wins the suffix test
        if raw.endswith(unit):
            num = raw[: -len(unit)].strip()
            if not num.isdigit():
                raise ScenarioError(f"{scenario}: invalid duration {raw!r}")
            return int(num) * _DURATION_UNITS_MS[unit]
    if raw.isdigit():  # bare number -> seconds
        return int(raw) * 1000
    raise ScenarioError(f"{scenario}: invalid duration {raw!r} (use e.g. 60s, 500ms, 2m)")


def _parse_params(raw: str) -> dict[str, str]:
    """Parse ``k=v,k=v`` into a dict, rejecting malformed pairs."""
    params: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ScenarioError(f"scenario param {pair!r} is not k=v")
        key, _, value = pair.partition("=")
        params[key.strip()] = value.strip()
    return params


def _parse_int(params: dict[str, str], key: str, scenario: str) -> int:
    raw = params.get(key)
    if raw is None:
        raise ScenarioError(f"{scenario}: missing required param {key!r}")
    if not raw.lstrip("-").isdigit():
        raise ScenarioError(f"{scenario}: {key}={raw!r} is not an integer")
    return int(raw)


def parse_scenario(spec: str) -> FaultScenario:
    """Parse one ``--scenario`` spec (``<name>:<k=v,k=v>``) into a fresh scenario.

    Raises :class:`ScenarioError` for an unknown name, a missing/invalid param,
    or malformed syntax. Grammar per scenario:

    - ``pii_block:every=N``
    - ``injection:on-pattern=<substr>``
    - ``budget:limit=<tokens>,window=<dur>[,cost=<tokens>]``
    - ``request_limit:limit=<requests>,window=<dur>``
    """
    name, sep, rest = spec.partition(":")
    name = name.strip()
    if not sep:
        raise ScenarioError(f"scenario {spec!r} has no ':' — expected '<name>:<k=v,...>'")
    if name == "injection":
        params = _parse_params(rest)
        pattern = params.get("on-pattern")
        if pattern is None:
            raise ScenarioError("injection: missing required param 'on-pattern'")
        return InjectionScenario(pattern)
    if name == "pii_block":
        params = _parse_params(rest)
        return PiiBlockScenario(_parse_int(params, "every", "pii_block"))
    if name == "budget":
        params = _parse_params(rest)
        limit = _parse_int(params, "limit", "budget")
        window_raw = params.get("window")
        if window_raw is None:
            raise ScenarioError("budget: missing required param 'window'")
        window_ms = _parse_duration_ms(window_raw, "budget")
        cost = _parse_int(params, "cost", "budget") if "cost" in params else None
        return BudgetScenario(limit, window_ms, cost)
    if name == "request_limit":
        params = _parse_params(rest)
        limit = _parse_int(params, "limit", "request_limit")
        window_raw = params.get("window")
        if window_raw is None:
            raise ScenarioError("request_limit: missing required param 'window'")
        return RequestLimitScenario(limit, _parse_duration_ms(window_raw, "request_limit"))
    raise ScenarioError(
        f"unknown scenario {name!r}; supported: injection, pii_block, budget, request_limit"
    )


def parse_scenarios(specs: list[str]) -> tuple[FaultScenario, ...]:
    """Parse a list of ``--scenario`` specs into fresh scenario instances."""
    return tuple(parse_scenario(s) for s in specs)


def __getattr__(name: str) -> type[FaultScenario]:
    # Deprecated alias (#719): ``Scenario`` collided with ``conformance.Scenario``.
    if name == "Scenario":
        warnings.warn(
            "donkey_kit.simulator.scenarios.Scenario is deprecated; use FaultScenario.",
            DeprecationWarning,
            stacklevel=2,
        )
        return FaultScenario
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

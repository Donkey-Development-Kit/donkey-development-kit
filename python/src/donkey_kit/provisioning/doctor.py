"""``donkey doctor`` — turn "why doesn't this work" into a thirty-second answer (#202).

One governed probe call, read through the §2.4 error taxonomy, tells the three
failures that look identical from the outside apart:

* **wrong URL / unreachable gateway** — a transport-level failure with no HTTP
  response, surfaced by the transport as :class:`GatewayUnavailable` (BG §1.2).
* **wrong credentials** — the gateway answered ``401``/``403`` →
  :class:`AuthError`.
* **credentials fine, model rejected** — the request got past auth and the
  provider passthrough rejected the model (the LIVE-VERIFIED ``400``
  ``model_not_found`` shape, docs/verified-apis.md §4) → :class:`UpstreamRequestError`.

Every failure line prints the remediation string carried by the exception it
stands for, so the CLI and the taxonomy can never disagree (AC2, one source of
wording). The budget line always states ``observed_at`` staleness — the proxy
has no budget-query endpoint (upstream gap #2), so a budget is only ever as
fresh as the last response, and doctor never implies otherwise (AC3).

Honest scope (§0.3): the gateway's *allow-list* rejection (a model refused by
API Manager policy rather than missing at the provider) has no captured 403
shape yet, and enumerating the allowed alternatives needs the discovery
endpoint (upstream gap #3, out of scope per #202). So the model line diagnoses
the verified ``model_not_found`` passthrough and prints its remediation, but
does not list which other models are permitted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.config import _TOML_NAME, DonkeyConfig
from ..core.errors import (
    AuthError,
    ConfigError,
    DonkeyError,
    GatewayUnavailable,
    UpstreamRequestError,
)

if TYPE_CHECKING:
    from ..core.budget import Budget

#: The three llm-proxy fields ``config`` reports on (mirrors ``validated(need="llm")``).
_LLM_FIELDS = ("llm_proxy_url", "llm_proxy_client_id", "llm_proxy_client_secret")


class Level(str, Enum):
    """Severity of one diagnosis. ``FAIL`` is the only level that makes ``donkey
    doctor`` exit non-zero, so it stays usable as a CI preflight (AC4)."""

    OK = "ok"
    FAIL = "fail"
    INFO = "info"
    SKIP = "skip"


#: Left-margin glyph per level; matches the report shape in #202.
_GLYPH = {Level.OK: "[ok]", Level.FAIL: "[!!]", Level.INFO: "[i] ", Level.SKIP: "[--]"}


@dataclass(frozen=True)
class Check:
    """One line of the report: a named diagnosis, its level, a human detail, and
    (on a failure) the remediation lifted verbatim from the taxonomy."""

    name: str
    level: Level
    detail: str
    remediation: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    """The outcome of the single governed probe call. ``error`` is ``None`` on a
    clean success; otherwise it is the typed :class:`DonkeyError` the taxonomy
    mapped the failure to. ``budget`` is the Donkey's budget window as it stood
    after the probe (unobserved if nothing came back)."""

    error: DonkeyError | None
    budget: Budget | None


#: A probe takes the resolved config + the model to test and returns a
#: :class:`ProbeResult`. Injectable so the diagnosis logic is tested without a
#: live gateway; the default (:func:`_live_probe`) makes a real call.
Probe = Callable[[DonkeyConfig, str], ProbeResult]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _humanize(seconds: float) -> str:
    """Compact duration: ``42s`` / ``42m`` / ``2h`` / ``3d``. Sub-second and
    negative values collapse to ``0s`` so a stale-in-the-past reset never prints
    a minus sign."""
    s = int(max(0.0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _config_check(cfg: DonkeyConfig) -> tuple[Check, bool]:
    """Resolve config without any network call and report llm-proxy completeness.
    Returns the check plus whether it's safe to probe (all required fields set)."""
    resolved = sum(1 for f in _LLM_FIELDS if getattr(cfg, f))
    toml_present = (Path.cwd() / _TOML_NAME).is_file()
    source = f"{_TOML_NAME} + env" if toml_present else "env"
    try:
        cfg.validated(need="llm")
    except ConfigError as exc:
        # The ConfigError message already lists every missing field AND the ways
        # to set them — that IS the remediation, one source of wording.
        return (
            Check(
                "config",
                Level.FAIL,
                f"{source} ({resolved}/{len(_LLM_FIELDS)} llm fields)",
                remediation=str(exc),
            ),
            False,
        )
    return Check("config", Level.OK, f"{source} ({resolved} fields)"), True


def _probe_checks(result: ProbeResult) -> list[Check]:
    """Turn one probe outcome into the gateway / credentials / model lines. A
    downstream diagnosis that can't be reached (credentials when the gateway is
    unreachable) is reported ``[--]`` not-checked rather than guessed."""
    err = result.error
    gateway = Check("gateway", Level.OK, "reachable, responded")
    creds = Check("credentials", Level.OK, "client_id accepted")
    model = Check("model", Level.OK, "accepted by the proxy")

    if isinstance(err, GatewayUnavailable):
        where = err.base_url or "the configured URL"
        gateway = Check("gateway", Level.FAIL, f"unreachable — {where}", err.remediation)
        not_checked = "not checked — gateway unreachable"
        return [gateway, Check("credentials", Level.SKIP, not_checked),
                Check("model", Level.SKIP, not_checked)]

    if isinstance(err, AuthError):
        creds = Check("credentials", Level.FAIL, "rejected by the gateway", err.remediation)
        return [gateway, creds, Check("model", Level.SKIP, "not checked — credentials rejected")]

    # The LIVE-VERIFIED model rejection is the provider passthrough (400
    # model_not_found), which classify() maps to UpstreamRequestError carrying
    # code/param. A 403 allow-list rejection has no captured shape yet (§0.3) and
    # would surface as AuthError above — a known, documented limitation.
    if isinstance(err, UpstreamRequestError) and (
        err.code == "model_not_found" or err.param == "model"
    ):
        model = Check("model", Level.FAIL, str(err).split(": ", 1)[-1] or "rejected",
                      _model_remediation(err))
        return [gateway, creds, model]

    # Any other typed error (a policy refusal or an upstream 5xx on the probe):
    # auth and the model were both accepted; the failure is reported on its own
    # line so doctor stays honest about what it saw.
    if err is not None:
        detail = getattr(err, "remediation", None)
        return [gateway, creds, model,
                Check("policy", Level.INFO, str(err), detail)]

    return [gateway, creds, model]


def _model_remediation(err: UpstreamRequestError) -> str:
    """Prefer the exception's own remediation; UpstreamRequestError now carries a
    canonical one, so this is the single source of wording."""
    return getattr(err, "remediation", None) or (
        "The requested model was rejected by the proxy. Request it in API "
        "Manager, or choose a model this instance routes."
    )


def _budget_check(budget: Budget | None) -> Check:
    """The budget line: remaining/limit, reset, and — always — how stale the
    reading is. Never implies live data (AC3): the proxy has no budget endpoint,
    so ``observed_at`` is the only truth about freshness."""
    if budget is None or budget.observed_at is None:
        return Check("budget", Level.INFO,
                     "not yet observed — no call has returned a budget window")
    ago = _humanize((_utcnow() - budget.observed_at).total_seconds())
    remaining = f"{budget.remaining:,}" if budget.remaining is not None else "?"
    limit = f"{budget.limit:,}" if budget.limit is not None else "?"
    parts = [f"{remaining} / {limit} remaining"]
    if budget.reset_at is not None:
        parts.append(f"resets in {_humanize((budget.reset_at - _utcnow()).total_seconds())}")
    parts.append(f"observed {ago} ago")
    return Check("budget", Level.INFO, ", ".join(parts))


def _live_probe(cfg: DonkeyConfig, model: str) -> ProbeResult:
    """Make one real governed call and normalise every failure into a typed
    :class:`DonkeyError`. Transport failures already arrive as
    :class:`GatewayUnavailable` from our transport (BG §1.2); an HTTP error
    arrives from the raw client as ``openai.APIStatusError``, which we bridge
    through :func:`classify` exactly as a caller would."""
    from ..donkey import Donkey

    donkey = Donkey(cfg)
    try:
        client = donkey.openai(sync=True)
        try:
            client.responses.create(model=model, input="ping", max_output_tokens=16)
            return ProbeResult(None, donkey.budget)
        except GatewayUnavailable as exc:
            return ProbeResult(exc, donkey.budget)
        except DonkeyError as exc:
            return ProbeResult(exc, donkey.budget)
        except Exception as exc:  # noqa: BLE001 - bridge the raw client's errors
            return ProbeResult(_bridge(exc, cfg), donkey.budget)
    finally:
        donkey.close()


def _bridge(exc: Exception, cfg: DonkeyConfig) -> DonkeyError:
    """Map a raw-client exception into the taxonomy: an HTTP error via
    :func:`classify`, a transport error into :class:`GatewayUnavailable`."""
    from ..core.errors import classify, gateway_unavailable

    response = getattr(exc, "response", None)
    if response is not None:
        return classify(response)
    return gateway_unavailable(base_url=cfg.llm_proxy_url, cause=exc)


def run_diagnostics(model: str, *, probe: Probe | None = None) -> list[Check]:
    """Run every check and return the report lines. ``probe`` defaults to a live
    governed call; tests inject a canned :class:`ProbeResult` to exercise each
    diagnosis without a gateway."""
    cfg = DonkeyConfig.from_env()
    config_check, can_probe = _config_check(cfg)
    checks = [config_check]
    if not can_probe:
        nc = "not checked — config incomplete"
        checks += [Check(n, Level.SKIP, nc) for n in ("credentials", "gateway", "model")]
        checks.append(_budget_check(None))
        return checks

    result = (probe or _live_probe)(cfg, model)
    checks += _probe_checks(result)
    checks.append(_budget_check(result.budget))
    return checks


def format_report(checks: list[Check]) -> str:
    """Render the ``[ok] name  detail`` report, remediation indented under any
    failure — the exact shape #202 specifies."""
    width = max((len(c.name) for c in checks), default=0)
    lines: list[str] = []
    for c in checks:
        lines.append(f"{_GLYPH[c.level]} {c.name.ljust(width)}  {c.detail}")
        if c.remediation:
            lines.append(f"     remediation: {c.remediation}")
    return "\n".join(lines)


def has_failure(checks: list[Check]) -> bool:
    """True if any check is a hard failure — the CI-preflight exit signal (AC4)."""
    return any(c.level is Level.FAIL for c in checks)

"""``donkey doctor`` — turn "why doesn't this work" into a thirty-second answer (#202).

One governed probe call, read through the BG §1.2 error taxonomy, tells the three
failures that look identical from the outside apart:

* **wrong URL / unreachable gateway** — a transport-level failure with no HTTP
  response, surfaced by the transport as :class:`GatewayUnavailable` (BG §1.2).
* **wrong credentials** — the gateway answered ``401``/``403`` →
  :class:`AuthError`.
* **credentials fine, model rejected** — the request got past auth and the
  provider passthrough rejected the model (the ``400``
  ``model_not_found`` shape, docs/verified-apis.md §4) → :class:`UpstreamRequestError`.

Two ``404`` shapes are read from the response itself, not its error type
(#896, ``tests/fixtures/anypoint/azure_openai_routing/``):

* **no proxy on the base path** — an empty ``404`` with no ``x-llm-proxy-*``
  header. The gateway answered, but nothing is deployed at that path, so
  neither the credentials nor the model were checked.
* **``/responses`` not served on this route** — a ``404`` that carries the
  proxy's routing headers: the proxy accepted the credentials and routed the
  model, and the upstream (an Azure OpenAI route, docs/verified-apis.md §2) does not
  serve the Responses API. Doctor then makes one ``/chat/completions`` probe
  (docs/verified-apis.md §2, Chat Completions row) and reports the route as
  working if it answers.

Every failure line prints the remediation string carried by the exception it
stands for, so the CLI and the taxonomy can never disagree (AC2, one source of
wording). The budget line always states ``observed_at`` staleness — the proxy
has no budget-query endpoint (upstream gap #2), so a budget is only ever as
fresh as the last response, and doctor never implies otherwise (AC3).

Before any request, doctor prints each endpoint's host and where it came from
(env, project file, local overlay, user file or default). An LLM-proxy endpoint
that may not receive the configured credentials (see
:meth:`DonkeyConfig.check_endpoints`) fails the ``config`` line, so the probe
never runs. When ``DONKEY_ALLOW_HTTP`` is on, a ``plain http`` line shows the value set.

Honest scope (verification discipline): the gateway's *allow-list* rejection (a model refused by
API Manager policy rather than missing at the provider) has no captured 403
shape yet, and enumerating the allowed alternatives needs the discovery
endpoint (upstream gap #3, out of scope per #202). So the model line diagnoses
the verified ``model_not_found`` passthrough and prints its remediation, but
does not list which other models are permitted.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from ..core._wire import LLM_PROVIDER_HEADER, LLM_PROXY_HEADER_PREFIX
from ..core.config import ConfigSource, DonkeyConfig
from ..core.endpoints import allow_http_enabled, allow_http_setting, host_of
from ..core.errors import (
    AuthError,
    ConfigError,
    DonkeyError,
    GatewayUnavailable,
    UpstreamRequestError,
)
from ._app import app

if TYPE_CHECKING:
    import httpx

    from ..core.budget import Budget

__all__ = [
    "DoctorCheck",
    "Level",
    "Probe",
    "ProbeResult",
    "doctor",
    "format_report",
    "has_failure",
    "run_diagnostics",
]

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
class DoctorCheck:
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
    after the probe (unobserved if nothing came back). ``chat`` is the
    ``/chat/completions`` probe, made only when the proxy routed the model and
    the upstream answered ``/responses`` with ``404`` (#896)."""

    error: DonkeyError | None
    budget: Budget | None
    chat: ProbeResult | None = None


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


def _config_check(cfg: DonkeyConfig) -> tuple[DoctorCheck, bool]:
    """Resolve config without any network call and report llm-proxy completeness.
    Returns the check plus whether it's safe to probe (all required fields set)."""
    set_fields = [f for f in _LLM_FIELDS if getattr(cfg, f)]
    resolved = len(set_fields)
    labels = dict.fromkeys(str(cfg.source_of(f)) for f in set_fields)
    source = " + ".join(labels) or "nothing set"
    try:
        cfg.validated(need="llm")
    except ConfigError as exc:
        # The ConfigError message already lists every missing field AND the ways
        # to set them — that IS the remediation, one source of wording.
        return (
            DoctorCheck(
                "config",
                Level.FAIL,
                f"{source} ({resolved}/{len(_LLM_FIELDS)} llm fields)",
                remediation=str(exc),
            ),
            False,
        )
    return DoctorCheck("config", Level.OK, f"{source} ({resolved} fields)"), True


def _endpoint_detail(url: str, source: ConfigSource) -> str:
    return f"{host_of(url) or 'no host'} ({source})"


def _endpoint_checks(cfg: DonkeyConfig) -> list[DoctorCheck]:
    """One line per resolved endpoint: its host and where it came from. The
    probe only exercises the LLM proxy, so a control-plane problem is shown
    with its remediation but does not fail the report."""
    checks: list[DoctorCheck] = []
    if cfg.llm_proxy_url:
        checks.append(
            DoctorCheck(
                "llm endpoint",
                Level.INFO,
                _endpoint_detail(cfg.llm_proxy_url, cfg.source_of("llm_proxy_url")),
            )
        )
    remediation: str | None = None
    if cfg.client_id or cfg.client_secret:
        try:
            cfg.check_endpoints(need="control_plane")
        except ConfigError as exc:
            remediation = str(exc)
    checks.append(
        DoctorCheck(
            "control plane",
            Level.INFO,
            _endpoint_detail(cfg.control_plane_url, cfg.source_of("base_url")),
            remediation,
        )
    )
    if allow_http_enabled():
        checks.append(
            DoctorCheck(
                "plain http",
                Level.INFO,
                f"allowed to non-loopback hosts ({allow_http_setting()} in env)",
            )
        )
    return checks


#: Remediation for an empty 404: no proxy answers on the configured base path.
_UNSERVED_REMEDIATION = (
    "No proxy answers on this base path. Check that the path in "
    "DONKEY_LLM_PROXY_URL is the proxy's base path, with a trailing slash and "
    "no /v1, and that the proxy is Active in API Manager on a running gateway."
)

#: Remediation for a routed 404 on /responses: use Chat Completions instead.
_RESPONSES_REMEDIATION = (
    "This route's upstream does not serve the OpenAI Responses API. Call it "
    "with the Chat Completions API: client.chat.completions.create(...) on "
    "donkey.llm.client(), or a framework's chat-completions client."
)


def _not_found(err: DonkeyError | None) -> httpx.Response | None:
    """The 404 response behind ``err``, or ``None`` for any other outcome."""
    response = getattr(err, "response", None)
    return response if response is not None and response.status_code == 404 else None


def _proxy_headers_present(response: httpx.Response) -> bool:
    return any(k.lower().startswith(LLM_PROXY_HEADER_PREFIX) for k in response.headers)


def _unserved_base_path(err: DonkeyError | None) -> bool:
    """An empty 404 with no ``x-llm-proxy-*`` header: nothing is deployed at the
    configured base path (``reject.unserved-base-path``)."""
    response = _not_found(err)
    return response is not None and not response.content and not _proxy_headers_present(response)


def _route_not_served(err: DonkeyError | None) -> httpx.Response | None:
    """The 404 response when it carries the proxy's routing headers: the proxy
    routed the model and the upstream does not serve the path
    (``reject.responses-not-served``)."""
    response = _not_found(err)
    return response if response is not None and _proxy_headers_present(response) else None


def _probe_checks(result: ProbeResult) -> list[DoctorCheck]:
    """Turn one probe outcome into the gateway / credentials / model lines. A
    downstream diagnosis that can't be reached (credentials when the gateway is
    unreachable) is reported ``[--]`` not-checked rather than guessed."""
    err = result.error
    gateway = DoctorCheck("gateway", Level.OK, "reachable, responded")
    creds = DoctorCheck("credentials", Level.OK, "client_id accepted")
    model = DoctorCheck("model", Level.OK, "accepted by the proxy")

    if isinstance(err, GatewayUnavailable):
        where = err.base_url or "the configured URL"
        gateway = DoctorCheck("gateway", Level.FAIL, f"unreachable — {where}", err.remediation)
        not_checked = "not checked — gateway unreachable"
        return [gateway, DoctorCheck("credentials", Level.SKIP, not_checked),
                DoctorCheck("model", Level.SKIP, not_checked)]

    if isinstance(err, AuthError):
        creds = DoctorCheck("credentials", Level.FAIL, "rejected by the gateway", err.remediation)
        skipped = DoctorCheck("model", Level.SKIP, "not checked — credentials rejected")
        return [gateway, creds, skipped]

    # The model rejection is the provider passthrough (docs/verified-apis.md §4: 400
    # model_not_found), which classify() maps to UpstreamRequestError carrying
    # code/param. A 403 allow-list rejection has no captured shape yet (verification discipline) and
    # would surface as AuthError above — a known, documented limitation.
    if isinstance(err, UpstreamRequestError) and (
        err.code == "model_not_found" or err.param == "model"
    ):
        model = DoctorCheck("model", Level.FAIL, str(err).split(": ", 1)[-1] or "rejected",
                      _model_remediation(err))
        return [gateway, creds, model]

    if _unserved_base_path(err):
        gateway = DoctorCheck("gateway", Level.FAIL,
                              "reachable, but no proxy on this base path (empty 404)",
                              _UNSERVED_REMEDIATION)
        not_checked = "not checked — no proxy on this base path"
        return [gateway, DoctorCheck("credentials", Level.SKIP, not_checked),
                DoctorCheck("model", Level.SKIP, not_checked)]

    not_served = _route_not_served(err)
    if not_served is not None:
        provider = not_served.headers.get(LLM_PROVIDER_HEADER) or "upstream"
        responses = DoctorCheck(
            "responses", Level.INFO,
            f"/responses not served on this route (404 from the {provider} upstream)",
            _RESPONSES_REMEDIATION,
        )
        chat = result.chat
        if chat is None:
            model = DoctorCheck("model", Level.SKIP,
                                "not checked — /responses not served on this route")
            return [gateway, creds, model, responses]
        if _route_not_served(chat.error) is not None:
            model = DoctorCheck(
                "model", Level.FAIL,
                "routed, but the upstream serves neither /responses nor /chat/completions",
                _RESPONSES_REMEDIATION,
            )
            return [gateway, creds, model, responses]
        checks = _probe_checks(chat)
        checks.append(responses)
        if chat.error is None:
            checks.append(DoctorCheck("chat completions", Level.OK, "served on this route"))
        return checks

    # Any other typed error (a policy refusal or an upstream 5xx on the probe):
    # auth and the model were both accepted; the failure is reported on its own
    # line so doctor stays honest about what it saw.
    if err is not None:
        detail = getattr(err, "remediation", None)
        return [gateway, creds, model,
                DoctorCheck("policy", Level.INFO, str(err), detail)]

    return [gateway, creds, model]


def _model_remediation(err: UpstreamRequestError) -> str:
    """Prefer the exception's own remediation; UpstreamRequestError now carries a
    canonical one, so this is the single source of wording."""
    return getattr(err, "remediation", None) or (
        "The requested model was rejected by the proxy. Request it in API "
        "Manager, or choose a model this instance routes."
    )


def _budget_check(budget: Budget | None) -> DoctorCheck:
    """The budget line: remaining/limit, reset, and — always — how stale the
    reading is. Never implies live data (AC3): the proxy has no budget endpoint,
    so ``observed_at`` is the only truth about freshness."""
    if budget is None or budget.observed_at is None:
        return DoctorCheck("budget", Level.INFO,
                     "not yet observed — no call has returned a budget window")
    ago = _humanize((_utcnow() - budget.observed_at).total_seconds())
    remaining = f"{budget.remaining:,}" if budget.remaining is not None else "?"
    limit = f"{budget.limit:,}" if budget.limit is not None else "?"
    parts = [f"{remaining} / {limit} remaining"]
    if budget.reset_at is not None:
        parts.append(f"resets in {_humanize((budget.reset_at - _utcnow()).total_seconds())}")
    parts.append(f"observed {ago} ago")
    return DoctorCheck("budget", Level.INFO, ", ".join(parts))


def _live_probe(cfg: DonkeyConfig, model: str) -> ProbeResult:
    """Make one real governed call and normalise every failure into a typed
    :class:`DonkeyError`. A transport failure is raised by our transport as
    :class:`GatewayUnavailable` (BG §1.2) but reaches here wrapped in the OpenAI
    SDK's ``APIConnectionError``; an HTTP error arrives as
    ``openai.APIStatusError``. :func:`_bridge` unwraps or classifies both."""
    from ..donkey import Donkey

    donkey = Donkey(cfg)
    try:
        client = donkey.openai(sync=True)
        error = _attempt(
            lambda: client.responses.create(model=model, input="ping", max_output_tokens=16),
            cfg,
        )
        chat: ProbeResult | None = None
        if _route_not_served(error) is not None:
            # The proxy routed the model but the upstream has no /responses
            # (#896): try the other verified route once (docs/verified-apis.md §2).
            chat_error = _attempt(
                lambda: client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": "ping"}]
                ),
                cfg,
            )
            chat = ProbeResult(chat_error, donkey.budget)
        return ProbeResult(error, donkey.budget, chat)
    finally:
        donkey.close()


def _attempt(call: Callable[[], object], cfg: DonkeyConfig) -> DonkeyError | None:
    """Run one probe call; ``None`` on success, else the typed error."""
    try:
        call()
    except DonkeyError as exc:
        return exc
    except Exception as exc:  # noqa: BLE001 - bridge the raw client's errors
        return _bridge(exc, cfg)
    return None


def _bridge(exc: Exception, cfg: DonkeyConfig) -> DonkeyError:
    """Map a raw-client exception into the taxonomy. A typed error our transport
    raised inside ``send()`` (an outage, a closed client) arrives wrapped by the
    OpenAI SDK as ``APIConnectionError`` with the typed error on ``__cause__``
    (#813), so the cause chain is checked first. Otherwise: an HTTP error via
    :func:`classify`, a transport error into :class:`GatewayUnavailable`."""
    from ..core.errors import classify, gateway_unavailable

    cause = exc.__cause__
    while cause is not None:
        if isinstance(cause, DonkeyError):
            return cause
        cause = cause.__cause__

    response = getattr(exc, "response", None)
    if response is not None:
        return classify(response)
    return gateway_unavailable(base_url=cfg.llm_proxy_url, cause=exc)


def run_diagnostics(
    model: str, *, path: Path | None = None, probe: Probe | None = None
) -> list[DoctorCheck]:
    """Run every check and return the report lines. ``probe`` defaults to a live
    governed call; tests inject a canned :class:`ProbeResult` to exercise each
    diagnosis without a gateway. ``path`` replaces the project config file."""
    cfg = DonkeyConfig.resolve(path=path)
    config_check, can_probe = _config_check(cfg)
    checks = [config_check, *_endpoint_checks(cfg)]
    if not can_probe:
        nc = "not checked — config check failed"
        checks += [DoctorCheck(n, Level.SKIP, nc) for n in ("credentials", "gateway", "model")]
        checks.append(_budget_check(None))
        return checks

    result = (probe or _live_probe)(cfg, model)
    checks += _probe_checks(result)
    checks.append(_budget_check(result.budget))
    return checks


def format_report(checks: list[DoctorCheck]) -> str:
    """Render the ``[ok] name  detail`` report, remediation indented under any
    failure — the exact shape #202 specifies."""
    width = max((len(c.name) for c in checks), default=0)
    lines: list[str] = []
    for c in checks:
        lines.append(f"{_GLYPH[c.level]} {c.name.ljust(width)}  {c.detail}")
        if c.remediation:
            lines.append(f"     remediation: {c.remediation}")
    return "\n".join(lines)


def has_failure(checks: list[DoctorCheck]) -> bool:
    """True if any check is a hard failure — the CI-preflight exit signal (AC4)."""
    return any(c.level is Level.FAIL for c in checks)


@app.command()
def doctor(
    ctx: typer.Context,
    model: str = typer.Option(
        "gpt-4o", "--model", help="model id to test against the proxy allow-list"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit machine-readable JSON"),
) -> None:
    """Diagnose governed access: config, credentials, gateway, model, budget (#202).

    Makes ONE real governed call and reads the result through the BG §1.2 error
    taxonomy to tell the three look-alike failures apart — wrong URL
    (``GatewayUnavailable``), wrong credentials (``AuthError``), and
    credentials-fine-but-model-rejected (the verified ``model_not_found``
    passthrough → ``UpstreamRequestError``). Each failure prints the remediation
    the exception itself carries (one source of wording), and the budget line
    always states how stale the reading is — the proxy has no budget endpoint, so
    doctor never implies live data.

    Exits non-zero if any check fails, so it works as a CI preflight. Needs the
    ``[llm]`` extra for the probe client (a missing extra is an install prompt,
    exit 1, not a ``blocked on verification`` message).
    """
    # The global --json (before the subcommand) and the local --json (after it)
    # are equivalent — either turns on machine-readable output.
    as_json = as_json or bool((ctx.obj or {}).get("json"))

    try:
        config_path = (ctx.obj or {}).get("config")
        checks = run_diagnostics(model, path=config_path) if config_path else run_diagnostics(model)
    except ImportError as exc:  # openai (the [llm] extra) not installed
        typer.secho(
            'donkey doctor needs the [llm] extra for the probe client. Install it with:\n'
            '    pip install "donkey-kit[llm]"',
            fg="yellow",
            err=True,
        )
        raise typer.Exit(1) from exc
    except DonkeyError as exc:
        # A config/probe failure that escaped the taxonomy mapping: surface it
        # rather than crash with a traceback.
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from exc

    if as_json:
        typer.echo(
            json.dumps(
                [
                    {"name": c.name, "level": c.level.value, "detail": c.detail,
                     "remediation": c.remediation}
                    for c in checks
                ],
                indent=2,
            )
        )
    else:
        typer.echo(format_report(checks))

    if has_failure(checks):
        raise typer.Exit(1)

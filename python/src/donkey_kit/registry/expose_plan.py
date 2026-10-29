"""What ``donkey expose`` would put in front of one service, computed offline
(BG §2.9.2, ADR 0015, #226).

:func:`plan_exposure` picks one service from a
:class:`~donkey_kit.registry.publish_plan.PublishPlan` (an MCP server, or an
agent whose protocol is ``a2a_v1``) and reads how to expose it from a
``[donkey.expose]`` table in the scanned root's ``.donkey-kit.toml``, with the
``.donkey-kit.local.toml`` beside it overriding key by key::

    [donkey.expose]
    target = "shared-omni-gateway"          # the gateway's name; --target overrides

    [donkey.expose.services.support-triage]  # a service ID; every key is optional
    label    = "support-triage"              # instanceLabel and base path; default: the asset ID
    upstream = "https://triage.internal/"    # where the gateway reaches it; default: its endpoint

    [[donkey.expose.services.support-triage.policies]]
    asset = "<group id>/<policy asset id>/<version>"
    configuration = { injectMcpNameHeaders = false }   # default: {}
    outbound = false                                   # true: an A2A outbound policy

No policy is attached unless it is declared: which policies an instance needs
is the platform's call, not the SDK's (ADR 0015 rule 5). Makes no network call;
everything wrong is a :class:`~donkey_kit.registry.scan.ScanIssue`, so the
plan reports every problem at once.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..core.config import LOCAL_TOML_NAME, TOML_NAME
from .models import AssetRef
from .publish_plan import PlannedAsset, PublishPlan
from .scan import ScanIssue, _endpoint_problem

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 has no stdlib tomllib; the [core] dep ``tomli`` backfills it.
    import tomli as tomllib

__all__ = ["ExposePlan", "PlannedPolicy", "plan_exposure"]

_TABLE_KEYS = frozenset({"target", "services"})
_SERVICE_KEYS = frozenset({"label", "upstream", "policies"})
_POLICY_KEYS = frozenset({"asset", "configuration", "outbound"})
# The label is the instance's base path on the gateway (proxyUri
# ``http://0.0.0.0:<port>/<label>``), so it is one URL path segment.
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class PlannedPolicy:
    """One declared policy: its Exchange asset, its configuration, and whether
    it is an A2A outbound policy (applied to the instance's upstreams)."""

    asset: AssetRef
    configuration: dict[str, Any] = field(default_factory=dict)
    outbound: bool = False


@dataclass(frozen=True)
class ExposePlan:
    """The one service ``donkey expose`` would expose, and how.

    ``endpoint_type`` is the API Manager endpoint type, ``mcp`` or ``a2a``.
    ``asset`` is the service's planned Exchange asset; ``upstream`` is ``None``
    and ``target`` is ``None`` only when an issue says so. A plan with issues
    is not exposed: the publication plan's issues are carried over, since the
    registry step publishes it.
    """

    asset: PlannedAsset | None
    endpoint_type: Literal["mcp", "a2a"] | None = None
    label: str | None = None
    upstream: str | None = None
    target: str | None = None
    policies: tuple[PlannedPolicy, ...] = ()
    issues: tuple[ScanIssue, ...] = ()


def plan_exposure(
    publication: PublishPlan,
    root: str | Path,
    *,
    service: str | None = None,
    target: str | None = None,
) -> ExposePlan:
    """Plan exposing ``service`` (scanned from ``root``) offline.

    ``service`` may be left out when the publication plan has exactly one
    service. ``target`` overrides ``[donkey.expose] target``.
    """
    base = Path(root)
    declared = _Declaration(base.parent if base.is_file() else base)
    issues = [*publication.issues]
    asset = _pick(publication, service, declared)
    target = target or declared.target
    if not target:
        declared.issue(
            "missing-target",
            "donkey.expose",
            "name the gateway to deploy to: set target in [donkey.expose], or pass --target",
        )
    for service_id in sorted(set(declared.services) - {a.service_id for a in publication.assets}):
        declared.issue(
            "unknown-service",
            f"expose service {service_id}",
            f"no agent or MCP server has service ID {service_id!r}; fix the ID or remove the table",
        )
    if asset is None:
        return ExposePlan(None, target=target, issues=_sorted(issues, declared))

    entry = declared.services.get(asset.service_id, {})
    subject = f"expose service {asset.service_id}"
    endpoint_type: Literal["mcp", "a2a"] | None = None
    if asset.type == "mcp":
        endpoint_type = "mcp"
    elif asset.protocol == "a2a_v1":
        endpoint_type = "a2a"
    else:
        declared.issue(
            "not-exposable",
            subject,
            f"agent {asset.service_id!r} has protocol {asset.protocol!r}; the gateway "
            'fronts A2A agents only: set protocol = "a2a_v1" in [donkey.agents]',
        )
    label = entry.get("label") or asset.asset_id
    if not _LABEL.fullmatch(label):
        declared.issue(
            "invalid-expose",
            subject,
            f"label {label!r} is the instance's base path, so it must be letters, digits, "
            "'.', '_' or '-'; set label in its [donkey.expose.services] table",
        )
    upstream = entry.get("upstream") or asset.endpoint
    if upstream is None:
        declared.issue(
            "missing-upstream",
            subject,
            "set upstream in its [donkey.expose.services] table (or its endpoint) to the "
            "URL the gateway reaches it on",
        )
    policies: tuple[PlannedPolicy, ...] = entry.get("policies", ())
    if endpoint_type == "mcp" and any(p.outbound for p in policies):
        declared.issue(
            "invalid-expose",
            subject,
            "outbound policies apply to an A2A instance's upstreams; an MCP server's "
            "policies are inbound: remove outbound = true",
        )
    return ExposePlan(
        asset=asset,
        endpoint_type=endpoint_type,
        label=label,
        upstream=upstream,
        target=target,
        policies=policies,
        issues=_sorted(issues, declared),
    )


def _pick(
    publication: PublishPlan, service: str | None, declared: _Declaration
) -> PlannedAsset | None:
    ids = ", ".join(a.service_id for a in publication.assets) or "none"
    if service is not None:
        for asset in publication.assets:
            if asset.service_id == service:
                return asset
        declared.issue(
            "unknown-service", service, f"no agent or MCP server has this service ID; found {ids}"
        )
        return None
    if len(publication.assets) == 1:
        return publication.assets[0]
    declared.issue(
        "missing-service",
        "donkey expose",
        f"expose takes one service per run; name one of: {ids}",
    )
    return None


def _sorted(issues: list[ScanIssue], declared: _Declaration) -> tuple[ScanIssue, ...]:
    return tuple(
        sorted((*issues, *declared.issues), key=lambda i: (i.path, i.line, i.kind, i.subject))
    )


class _Declaration:
    """The ``[donkey.expose]`` tables beside the scanned code, read as
    ``[donkey.publication]`` is: the project file, then the local file over it."""

    def __init__(self, directory: Path) -> None:
        self.issues: list[ScanIssue] = []
        self.target: str | None = None
        self.services: dict[str, dict[str, Any]] = {}
        self._file = TOML_NAME
        for name in (TOML_NAME, LOCAL_TOML_NAME):
            path = directory / name
            if path.is_file():
                self._read(path, name)

    def issue(self, kind: str, subject: str, detail: str) -> None:
        self.issues.append(ScanIssue(kind, subject, self._file, 0, detail))

    def _read(self, path: Path, name: str) -> None:
        # Unreadable or malformed files are already scan issues.
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            return
        donkey = data.get("donkey", {})
        table = donkey.get("expose") if isinstance(donkey, dict) else None
        if table is None:
            return
        self._file = name
        if not isinstance(table, dict):
            self.issue("invalid-expose", "donkey.expose", "must be a table")
            return
        for key in sorted(set(table) - _TABLE_KEYS):
            self.issue(
                "invalid-expose",
                "donkey.expose",
                f"unknown key {key!r}; the keys are {', '.join(sorted(_TABLE_KEYS))}",
            )
        if "target" in table:
            value = table["target"]
            if isinstance(value, str) and value.strip():
                self.target = value
            else:
                self.issue(
                    "invalid-expose",
                    "donkey.expose",
                    f"target must be a non-empty string, not {value!r}",
                )
        services = table.get("services", {})
        if not isinstance(services, dict):
            self.issue("invalid-expose", "donkey.expose.services", "must be a table")
            return
        for service_id, entry in services.items():
            self._service(service_id, entry)

    def _service(self, service_id: str, entry: object) -> None:
        subject = f"expose service {service_id}"
        if not isinstance(entry, dict):
            self.issue("invalid-expose", subject, "must be a table")
            return
        merged = self.services.setdefault(service_id, {})
        for key, value in entry.items():
            if key not in _SERVICE_KEYS:
                self.issue(
                    "invalid-expose",
                    subject,
                    f"unknown key {key!r}; the keys are {', '.join(sorted(_SERVICE_KEYS))}",
                )
            elif key == "policies":
                policies = self._policies(value, subject)
                if policies is not None:
                    merged[key] = policies
            elif key == "upstream":
                problem = _endpoint_problem(value, None)
                if problem is None:
                    merged[key] = value
                else:
                    self.issue("invalid-expose", subject, problem.replace("endpoint", "upstream"))
            elif isinstance(value, str) and value.strip():
                merged[key] = value
            else:
                self.issue(
                    "invalid-expose", subject, f"{key} must be a non-empty string, not {value!r}"
                )

    def _policies(self, value: object, subject: str) -> tuple[PlannedPolicy, ...] | None:
        if not isinstance(value, list):
            self.issue("invalid-expose", subject, "policies must be an array of tables")
            return None
        policies: list[PlannedPolicy] = []
        seen: set[tuple[str, str]] = set()
        ok = True
        for n, entry in enumerate(value, 1):
            where = f"{subject}, policy {n}"
            if not isinstance(entry, dict):
                self.issue("invalid-expose", where, "must be a table")
                ok = False
                continue
            for key in sorted(set(entry) - _POLICY_KEYS):
                self.issue(
                    "invalid-expose",
                    where,
                    f"unknown key {key!r}; the keys are {', '.join(sorted(_POLICY_KEYS))}",
                )
                ok = False
            ref = entry.get("asset")
            try:
                if not isinstance(ref, str):
                    raise ValueError(ref)
                asset = AssetRef.parse(ref)
            except ValueError:
                self.issue(
                    "invalid-expose",
                    where,
                    "asset must be the policy's Exchange coordinates, "
                    f"'<group id>/<asset id>/<version>', not {ref!r}",
                )
                ok = False
                continue
            configuration = entry.get("configuration", {})
            outbound = entry.get("outbound", False)
            if not isinstance(configuration, dict):
                self.issue("invalid-expose", where, "configuration must be a table")
                ok = False
            if not isinstance(outbound, bool):
                self.issue(
                    "invalid-expose", where, f"outbound must be true or false, not {outbound!r}"
                )
                ok = False
            key = (asset.group_id, asset.asset_id)
            if key in seen:
                self.issue("invalid-expose", where, f"{asset.asset_id} is declared twice; keep one")
                ok = False
            seen.add(key)
            if isinstance(configuration, dict) and isinstance(outbound, bool):
                policies.append(PlannedPolicy(asset, dict(configuration), outbound))
        return tuple(policies) if ok else None

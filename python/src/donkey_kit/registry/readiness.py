"""Offline readiness of a project's declarations, per workflow (#1749).

A valid ``.donkey-kit.toml`` is not yet a publishable or exposable one:
commented placeholders parse, and an undeclared MCP transport is deliberately
not a scan failure. :func:`assess` therefore reports three things separately:

* **scan**: whether the static scan (and the project file it reads) is valid;
* **publication**: whether ``donkey publish`` has what it needs, project-wide,
  since one publish registers every service;
* **exposure**: for each service, whether ``donkey expose`` has what it needs.

It reuses the planners (:func:`~donkey_kit.registry.publish_plan.plan_publication`,
:func:`~donkey_kit.registry.expose_plan.plan_exposure`) rather than restating
their rules, and names each missing value by its table and key. A value still
shaped like a scaffold placeholder (``"<a semantic version>"``) counts as
missing, not as declared.

It reads the committed project file alone, never the local overlay, the
user-wide config or the environment, and makes no network call. So it is not
live verification: whether the credentials, scopes, gateway and upstreams
work is ``not-checked`` here (``donkey doctor``, ``donkey preflight`` and
``donkey expose --dry-run`` check those). A group ID the configured ``org_id``
can supply is reported as a non-required gap for that reason.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.toml_text import key
from .expose_plan import plan_exposure
from .publish_plan import plan_publication
from .scan import ScanIssue, ScanResult

__all__ = [
    "BLOCKED",
    "INCOMPLETE",
    "INVALID",
    "NOT_EXPOSABLE",
    "READY",
    "Missing",
    "Readiness",
    "WorkflowReadiness",
    "assess",
]

#: Everything the workflow needs is declared (live state is not checked).
READY = "ready"
#: Required values are missing; ``missing`` lists them.
INCOMPLETE = "incomplete"
#: A declaration is wrong; ``problems`` says how.
INVALID = "invalid"
#: The scan reports an issue, so the workflow cannot be assessed until it is fixed.
BLOCKED = "blocked"
#: The gateway does not front this service: an agent whose protocol is not ``a2a_v1``.
NOT_EXPOSABLE = "not-exposable"

# What a scaffold placeholder looks like once uncommented: "<what goes here>".
_PLACEHOLDER = re.compile(r"<[^<>]+>")


@dataclass(frozen=True)
class Missing:
    """A value a workflow needs that the project file does not declare.

    ``table`` is the table's header text (``donkey.publication``) and ``key``
    the key in it. ``required`` is ``False`` for a gap something else can fill,
    such as a group ID the configured ``org_id`` supplies at publish time.
    """

    table: str
    key: str
    detail: str
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        """The gap as plain JSON-ready data."""
        return {
            "table": self.table,
            "key": self.key,
            "detail": self.detail,
            "required": self.required,
        }


@dataclass(frozen=True)
class WorkflowReadiness:
    """One workflow's offline state: :data:`READY`, :data:`INCOMPLETE`,
    :data:`INVALID`, :data:`BLOCKED` or :data:`NOT_EXPOSABLE`.

    ``service`` is the service ID for an exposure, ``None`` for publication.
    ``problems`` are the planner's issues that are not a missing value.
    """

    status: str
    missing: tuple[Missing, ...] = ()
    problems: tuple[ScanIssue, ...] = ()
    service: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The state as plain JSON-ready data."""
        data: dict[str, Any] = {
            "status": self.status,
            "missing": [m.to_dict() for m in self.missing],
            "problems": [p.to_dict() for p in self.problems],
        }
        if self.service is not None:
            data["service"] = self.service
        return data


@dataclass(frozen=True)
class Readiness:
    """Scan validity, publication readiness and per-service exposure readiness.

    ``publication`` is ``None`` and ``exposure`` empty when the scan found no
    service. ``live`` is always ``not-checked``: nothing here contacts a gateway.
    """

    scan_valid: bool
    publication: WorkflowReadiness | None = None
    exposure: tuple[WorkflowReadiness, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """The readiness as plain JSON-ready data."""
        return {
            "scan": "valid" if self.scan_valid else "invalid",
            "publication": self.publication.to_dict() if self.publication else None,
            "exposure": [e.to_dict() for e in self.exposure],
            "live": "not-checked",
        }


def assess(
    result: ScanResult,
    root: str | Path,
    project: dict[str, Any] | None,
    *,
    services: Collection[str] | None = None,
) -> Readiness:
    """The offline readiness of ``root``'s committed declarations.

    ``result`` is ``root``'s overlay-free scan and ``project`` the parsed
    project file (``{}`` when absent, ``None`` when it cannot be parsed).
    ``services`` limits the exposure entries to those service IDs; publication
    stays project-wide. Makes no network call and reads no overlay.
    """
    scan_valid = not result.issues and project is not None
    plan = plan_publication(result, root, local_overlay=False)
    if not plan.assets:
        return Readiness(scan_valid)
    placeholders = _placeholders(project or {})
    scanned = set(result.issues)
    publication = _publication(plan.issues, scanned, placeholders, result, scan_valid=scan_valid)

    planned = set(plan.issues)
    exposure: list[WorkflowReadiness] = []
    for asset in plan.assets:
        sid = asset.service_id
        if services is not None and sid not in services:
            continue
        exposed = plan_exposure(plan, root, service=sid, local_overlay=False)
        own = [i for i in exposed.issues if i not in planned]
        exposure.append(_exposure(sid, own, placeholders, result, scan_valid=scan_valid))
    return Readiness(scan_valid, publication, tuple(exposure))


def _publication(
    issues: tuple[ScanIssue, ...],
    scanned: set[ScanIssue],
    placeholders: dict[tuple[str, str], str],
    result: ScanResult,
    *,
    scan_valid: bool,
) -> WorkflowReadiness:
    missing = [
        Missing(table, k, f"still the placeholder {value}")
        for (table, k), value in placeholders.items()
        if table == "donkey.publication" or table.startswith("donkey.publication.")
    ]
    problems: list[ScanIssue] = []
    for issue in issues:
        if issue in scanned or _covered(issue, placeholders):
            continue
        if issue.kind == "missing-version":
            missing.append(
                Missing("donkey.publication", "version", "a semantic version, such as 1.0.0")
            )
        elif issue.kind == "missing-group-id":
            missing.append(
                Missing(
                    "donkey.publication",
                    "group_id",
                    "not declared: publish uses the configured org_id (ANYPOINT_ORG_ID), "
                    "which is not checked offline",
                    required=False,
                )
            )
        else:
            problems.append(issue)
    # Published regardless, but not as the developer may expect: say so.
    for agent in result.agents:
        if agent.protocol is None:
            missing.append(
                Missing(
                    _table("donkey", "agents", agent.name),
                    "protocol",
                    "not declared: the agent is published with protocol other",
                    required=False,
                )
            )
    for server in result.mcp_servers:
        if server.transport is None:
            missing.append(
                Missing(
                    _table("donkey", "mcp_servers", server.id),
                    "transport",
                    "not declared: the server is published without a transport",
                    required=False,
                )
            )
    return WorkflowReadiness(
        _status(missing, problems, scan_valid=scan_valid), _sorted(missing), tuple(problems)
    )


def _exposure(
    sid: str,
    issues: list[ScanIssue],
    placeholders: dict[tuple[str, str], str],
    result: ScanResult,
    *,
    scan_valid: bool,
) -> WorkflowReadiness:
    service_table = _table("donkey", "expose", "services", sid)
    missing = [
        Missing(table, k, f"still the placeholder {value}")
        for (table, k), value in placeholders.items()
        if table in {"donkey.expose", service_table}
    ]
    agent = next((a for a in result.agents if a.name == sid), None)
    owner = _table("donkey", "agents", sid) if agent else _table("donkey", "mcp_servers", sid)
    problems: list[ScanIssue] = []
    not_exposable = False
    for issue in issues:
        if _covered(issue, placeholders):
            continue
        if issue.kind == "missing-target":
            missing.append(
                Missing("donkey.expose", "target", "the gateway to deploy to, or pass --target")
            )
        elif issue.kind == "missing-upstream":
            missing.append(
                Missing(
                    service_table,
                    "upstream",
                    f"the URL the gateway reaches it on, or set endpoint in [{owner}]",
                )
            )
        elif issue.kind == "not-exposable" and agent is not None and agent.protocol is None:
            missing.append(
                Missing(owner, "protocol", 'not declared: set "a2a_v1" to expose the agent')
            )
        elif issue.kind == "not-exposable":
            not_exposable = True
            problems.append(issue)
        else:
            problems.append(issue)
    if scan_valid and not_exposable:
        # A deliberate non-A2A protocol: nothing else matters for exposure.
        reason = tuple(p for p in problems if p.kind == "not-exposable")
        return WorkflowReadiness(NOT_EXPOSABLE, (), reason, sid)
    status = _status(missing, problems, scan_valid=scan_valid)
    return WorkflowReadiness(status, _sorted(missing), tuple(problems), sid)


def _status(missing: list[Missing], problems: list[ScanIssue], *, scan_valid: bool) -> str:
    if not scan_valid:
        return BLOCKED
    if problems:
        return INVALID
    if any(m.required for m in missing):
        return INCOMPLETE
    return READY


def _covered(issue: ScanIssue, placeholders: dict[tuple[str, str], str]) -> bool:
    """An issue about a placeholder value, which is reported as missing instead."""
    return any(repr(value) in issue.detail for value in placeholders.values())


def _sorted(missing: list[Missing]) -> tuple[Missing, ...]:
    unique = {(m.table, m.key): m for m in missing}
    return tuple(unique[k] for k in sorted(unique))


def _table(*parts: str) -> str:
    return ".".join(key(p) for p in parts)


def _placeholders(project: dict[str, Any]) -> dict[tuple[str, str], str]:
    """Every ``"<...>"`` string in the publication and expose tables, keyed by
    (table, key); a key inside an inline table is dotted (``provider.url``)."""
    donkey = project.get("donkey")
    if not isinstance(donkey, dict):
        return {}
    found: dict[tuple[str, str], str] = {}
    for name in ("publication", "expose"):
        table = donkey.get(name)
        if isinstance(table, dict):
            for path, k, value in _strings(("donkey", name), table):
                if _PLACEHOLDER.fullmatch(value.strip()):
                    found[(_table(*path), k)] = value
    return found


def _strings(
    path: tuple[str, ...], table: dict[str, Any], prefix: str = ""
) -> Iterator[tuple[tuple[str, ...], str, str]]:
    for k, value in table.items():
        if isinstance(value, str):
            yield path, prefix + k, value
        elif isinstance(value, dict):
            # Sub-tables that name services are tables of their own; an inline
            # value table (provider) belongs to the key that holds it.
            if k in {"assets", "services"} and not prefix:
                for sid, entry in value.items():
                    if isinstance(entry, dict):
                        yield from _strings((*path, k, sid), entry)
            else:
                yield from _strings(path, value, f"{prefix}{k}.")

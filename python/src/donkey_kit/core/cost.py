"""Cost-attribution tags (docs/verified-apis.md §3, BG §1.7, #196).

A small, FIXED set of dimensions — ``team``, ``project``, ``env``,
``enduser.id`` — set once on the :class:`~donkey_kit.core.config.DonkeyConfig`
(and optionally overridden per run via ``donkey.run(...)``), plus any custom
dimensions declared once with ``cost_dimensions`` (#316), then emitted on every
governed model call:

  * as ``donkey.cost.*`` **span attributes**, which carry the full value
    because the SDK controls the span end to end — the authoritative carrier; and
  * as request **headers**, only when ``DonkeyConfig.send_cost_headers`` is
    enabled. The gateway ingests no cost-tag header (a verified-negative result,
    ``docs/verified-apis.md`` §3, #522), so the names are an overridable
    convention and the default sends none.

The key set is fixed on purpose (BG §1.7 / #196 AC #1): "we don't know" is the
failure this replaces, so an unknown dimension is a configuration *error*, not
a header we silently drop. Values are length-limited and reject control
characters so a tag can never smuggle JSON or inject a second header (AC #2).

Custom dimensions (#316) keep that rule. A name is declared once, at
construction (``Donkey.from_env(cost_dimensions=["cost_center"])``), and a value
for any other name raises at the call that passed it. :class:`DimensionGuard`
holds one client's declared set and enforces a per-dimension cardinality
ceiling, which a dimension declared in ``high_cardinality_cost_dimensions``
opts out of.
"""

from __future__ import annotations

import difflib
import os
import re
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from . import _verify
from .errors import ConfigError

__all__ = [
    "DIMENSION_CARDINALITY_CEILING",
    "MAX_COST_DIMENSIONS",
    "CostTags",
    "validate_tag_value",
]

# The fixed external key names (what a user writes in ``[donkey.cost]`` toml or a
# mapping) → the Python field. ``enduser.id`` keeps the OTel-style dotted name
# as its external key; the field is the identifier-safe ``enduser_id``.
_KEY_TO_FIELD: dict[str, str] = {
    "team": "team",
    "project": "project",
    "env": "env",
    "enduser.id": "enduser_id",
}

# The fixed fields, in declaration order (``dimensions`` is not one of them).
_FIXED: tuple[str, ...] = tuple(_KEY_TO_FIELD.values())

# Custom dimension names (#316): a lowercase identifier, so the name is safe as a
# span attribute suffix (``donkey.cost.<name>``) and as a header-name segment. A
# built-in name is refused, so ``donkey.cost.team`` can only ever mean one thing.
_DIMENSION_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
_RESERVED_NAMES = frozenset({*_FIXED, "enduser"})

#: The most custom dimensions one client may declare (#316). Each one is a new
#: grouping key in every backend that reads the spans, so the set stays small.
MAX_COST_DIMENSIONS = 10

#: The distinct values one client accepts for a custom dimension that is not
#: declared high-cardinality, before it raises (#316). A dimension that takes
#: thousands of values (a request id, a case number) multiplies the series in
#: any metrics backend that groups by it.
DIMENSION_CARDINALITY_CEILING = 1000

# A tag value long enough to be a smuggled document is refused (AC #2). 256 is
# generous for team/project/env/user identifiers and well under any header-size
# limit.
_MAX_VALUE_LEN = 256


def validate_tag_value(field: str, value: str, *, kind: str = "Cost tag") -> None:
    """Reject a value that could not be a legitimate attribution tag: empty,
    over-long, or carrying a control character (a newline is header injection;
    any control char has no place in a dimension value). Raises
    :class:`ConfigError` naming the offending field. ``kind`` labels it, so the
    per-request business group (#310) is refused by the same rule."""
    if value == "":
        raise ConfigError(
            f"{kind} {field!r} is empty. Omit it, or give it a value — an "
            f"empty tag is a config mistake, not 'no tag' (that is unset)."
        )
    if len(value) > _MAX_VALUE_LEN:
        raise ConfigError(
            f"{kind} {field!r} is {len(value)} chars; the maximum is "
            f"{_MAX_VALUE_LEN}. A cost dimension is a short label, not a payload."
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ConfigError(
            f"{kind} {field!r} contains a control character. Tags are emitted "
            f"as request headers, so a newline or control char would corrupt the "
            f"request — use a plain short label."
        )


@dataclass(frozen=True)
class CostTags:
    """The fixed four-dimension cost-attribution set (docs/verified-apis.md §3, #196),
    plus the values of any custom dimensions (#316).

    Every field is optional and defaults to ``None`` (unset — omitted from both
    headers and span attributes). Construction validates every set value, so an
    invalid tag is caught where it is configured, not on the first request.

    ``dimensions`` holds custom dimension values as ``(name, value)`` pairs,
    sorted by name. Set them through ``donkey.attribution(cost_center=...)``,
    which checks each name against the client's declared ``cost_dimensions``;
    this class checks only that each name and value is well formed.
    """

    team: str | None = None
    project: str | None = None
    env: str | None = None
    enduser_id: str | None = None
    dimensions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name in _FIXED:
            value = getattr(self, name)
            if value is not None:
                validate_tag_value(name, value)
        if self.dimensions:
            object.__setattr__(self, "dimensions", _dimension_pairs(self.dimensions))

    # ---------------------------------------------------------------- factory
    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object], *, source: str = "cost tags") -> CostTags:
        """Build from a mapping keyed by the FIXED external names
        (``team`` / ``project`` / ``env`` / ``enduser.id``). Any other key is a
        :class:`ConfigError` listing EVERY unknown key at once (AC #1) — the
        toml / mapping path is exactly where a typo'd dimension would otherwise
        vanish silently."""
        unknown = [k for k in mapping if k not in _KEY_TO_FIELD]
        if unknown:
            joined = ", ".join(sorted(unknown))
            allowed = ", ".join(sorted(_KEY_TO_FIELD))
            raise ConfigError(
                f"Unknown {source} key(s): {joined}. The cost-attribution key "
                f"set is fixed: {allowed}."
            )
        kwargs: dict[str, Any] = {
            _KEY_TO_FIELD[k]: (None if v is None else str(v)) for k, v in mapping.items()
        }
        return cls(**kwargs)

    # --------------------------------------------------------------- combine
    def merge(self, other: CostTags) -> CostTags:
        """Return ``self`` with every field ``other`` sets applied on top —
        per field, a set value in ``other`` wins, an unset one leaves ``self``
        untouched. This is the run-scope-over-config precedence: a
        ``donkey.run(project=...)`` overrides only ``project`` for its block and
        inherits the rest from the configured tags."""
        overrides: dict[str, object] = {
            name: getattr(other, name) for name in _FIXED if getattr(other, name) is not None
        }
        if other.dimensions:
            # Per dimension too: an inner scope's cost_center wins, the rest stay.
            merged = dict(self.dimensions) | dict(other.dimensions)
            overrides["dimensions"] = tuple(sorted(merged.items()))
        if not overrides:
            return self
        return replace(self, **overrides)  # type: ignore[arg-type]

    # ------------------------------------------------------------- accessors
    @property
    def is_empty(self) -> bool:
        """True when no cost-attribution dimension is set, fixed or custom."""
        return not self.dimensions and all(getattr(self, name) is None for name in _FIXED)

    def items(self) -> Iterator[tuple[str, str]]:
        """Yield ``(field_name, value)`` for each SET fixed field, in declaration
        order — what the transport iterates to emit one header per dimension.
        Custom dimensions are in :attr:`dimensions`."""
        for name in _FIXED:
            value = getattr(self, name)
            if value is not None:
                yield name, value

    def span_kwargs(self) -> dict[str, object]:
        """The set fields as ``build_genai_attributes`` keyword arguments
        (``team`` → ``cost_team`` …, custom ones as ``cost_dimensions``), so the
        span carries ``donkey.cost.*`` for every set dimension."""
        kwargs: dict[str, object] = {f"cost_{name}": value for name, value in self.items()}
        if self.dimensions:
            kwargs["cost_dimensions"] = self.dimensions
        return kwargs


def dimension_name_problem(name: object) -> str | None:
    """What a custom dimension name must be, when ``name`` is not one; else ``None``."""
    if not isinstance(name, str) or not _DIMENSION_NAME.fullmatch(name):
        return (
            "a dimension name of 1-32 lowercase letters, digits and underscores, "
            "starting with a letter"
        )
    if name in _RESERVED_NAMES:
        return f"a name other than the built-in dimension {name!r}"
    return None


def _dimension_pairs(pairs: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """``pairs`` as sorted ``(name, value)`` tuples, each name and value checked."""
    items = list(pairs.items() if isinstance(pairs, Mapping) else pairs)
    names = [name for name, _ in items]
    for name, value in items:
        problem = dimension_name_problem(name)
        if problem is not None:
            raise ConfigError(f"Cost dimension {name!r} is not valid: expected {problem}.")
        if not isinstance(value, str):
            raise ConfigError(
                f"Cost dimension {name!r} is {type(value).__name__}; expected a string."
            )
        validate_tag_value(name, value, kind="Cost dimension")
    if len(set(names)) != len(names):
        raise ConfigError(f"Cost dimensions name one dimension twice: {sorted(names)}.")
    return tuple(sorted(items))


def dimension_names_problem(names: object) -> str | None:
    """What a declared dimension list must be, when ``names`` is not one; else ``None``.

    The config check for ``cost_dimensions`` and
    ``high_cardinality_cost_dimensions`` (#316)."""
    if isinstance(names, str) or not isinstance(names, (list, tuple)):
        return "a list of dimension names"
    if len(names) > MAX_COST_DIMENSIONS:
        return f"at most {MAX_COST_DIMENSIONS} dimension names, not {len(names)}"
    for name in names:
        problem = dimension_name_problem(name)
        if problem is not None:
            return f"{problem}, not {name!r}"
    if len(set(names)) != len(names):
        return "each dimension name once"
    return None


def dimension_names(raw: object) -> tuple[str, ...]:
    """A declared dimension list from the environment (comma-separated) or TOML (a list)."""
    if isinstance(raw, str):
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    if isinstance(raw, (list, tuple)):
        return tuple(str(part) for part in raw)
    raise ValueError("expected a list of names, or a comma-separated string")


def check_dimensions(
    declared: Sequence[str], high_cardinality: Sequence[str], cost: CostTags
) -> None:
    """Refuse a high-cardinality opt-in, or a configured value, for a dimension that
    is not declared (#316). The per-name checks run first, in the config's field table."""
    unknown = sorted(set(high_cardinality) - set(declared))
    if unknown:
        raise ConfigError(
            f"high_cardinality_cost_dimensions names {', '.join(unknown)}, which "
            "cost_dimensions does not declare. Add each name to cost_dimensions too."
        )
    pairs = cost.dimensions if isinstance(cost, CostTags) else ()
    undeclared = sorted({name for name, _ in pairs} - set(declared))
    if undeclared:
        raise ConfigError(_undeclared_message(undeclared, declared, "the configured cost tags"))


def dimension_header(name: str) -> str:
    """The request header for the custom cost dimension ``name`` (#316): the
    ``X-Anypoint-Cost-`` prefix plus the title-cased name (``cost_center`` →
    ``X-Anypoint-Cost-Cost-Center``). A convention, like the fixed four: the gateway
    reads none of them (docs/verified-apis.md §3), and there is no per-name override."""
    suffix = "-".join(part.capitalize() for part in name.split("_") if part)
    return f"{_verify.COST_DIMENSION_HEADER_PREFIX}{suffix}"


def _undeclared_message(names: Sequence[str], declared: Sequence[str], where: str) -> str:
    parts = []
    for name in names:
        close = difflib.get_close_matches(name, declared, n=1)
        parts.append(f"{name!r} (did you mean {close[0]!r}?)" if close else repr(name))
    known = ", ".join(declared) if declared else "none"
    return (
        f"Unknown cost dimension(s) in {where}: {', '.join(parts)}. Declared: {known}. "
        "Declare each custom dimension once, when building the client: "
        "Donkey.from_env(cost_dimensions=[...])."
    )


class DimensionGuard:
    """One client's declared custom dimensions and their cardinality ceiling (#316).

    :meth:`tags` turns the custom keyword arguments of ``donkey.attribution()`` or
    ``donkey.run()`` into :class:`CostTags`. It raises :class:`ConfigError` for an
    undeclared name, naming it, and for a value that would take a dimension past
    :data:`DIMENSION_CARDINALITY_CEILING` distinct values in this client, unless
    that dimension is declared high-cardinality. Thread-safe."""

    def __init__(self, declared: Sequence[str], high_cardinality: Sequence[str] = ()) -> None:
        self._declared = tuple(declared)
        self._seen: dict[str, set[str]] = {
            name: set() for name in declared if name not in high_cardinality
        }
        self._lock = threading.Lock()

    def tags(self, values: Mapping[str, str | None], *, where: str) -> CostTags:
        """The custom ``values`` as :class:`CostTags`; a ``None`` value is unset."""
        undeclared = sorted(name for name in values if name not in self._declared)
        if undeclared:
            raise ConfigError(_undeclared_message(undeclared, self._declared, where))
        tags = CostTags(dimensions=tuple((k, v) for k, v in values.items() if v is not None))
        with self._lock:
            for name, value in tags.dimensions:
                seen = self._seen.get(name)
                if seen is None or value in seen:
                    continue
                if len(seen) >= DIMENSION_CARDINALITY_CEILING:
                    raise ConfigError(
                        f"Cost dimension {name!r} has already taken "
                        f"{DIMENSION_CARDINALITY_CEILING} distinct values in this client, the "
                        "ceiling for a dimension not declared high-cardinality. Every distinct "
                        "value is a new series in a backend that groups by it. If that is "
                        f"intended, declare high_cardinality_cost_dimensions=[{name!r}]."
                    )
                seen.add(value)
        return tags


# The four cost dimensions: field name, [donkey.cost] key and the env var that
# overrides each, in field order.
COST_KEYS: tuple[tuple[str, str, str], ...] = (
    ("team", "team", "DONKEY_COST_TEAM"),
    ("project", "project", "DONKEY_COST_PROJECT"),
    ("env", "env", "DONKEY_COST_ENV"),
    ("enduser_id", "enduser.id", "DONKEY_COST_ENDUSER_ID"),
)


def resolve_cost_tags(toml_cost: object) -> CostTags:
    """Resolve the cost tags along the fixed precedence: ``[donkey.cost]`` toml
    table is the base (its keys validated against the fixed set — an unknown
    dimension raises :class:`ConfigError`, never a silent drop, #196 AC #1), then
    ``DONKEY_COST_*`` env vars override per dimension. Absent both → empty tags."""
    if toml_cost is None:
        base = CostTags()
    elif isinstance(toml_cost, dict):
        base = CostTags.from_mapping(toml_cost, source="[donkey.cost]")
    else:
        raise ConfigError("[donkey.cost] must be a table of cost-attribution tags.")
    env_over: dict[str, Any] = {
        name: os.environ[var] for name, _, var in COST_KEYS if var in os.environ
    }
    return base.merge(CostTags(**env_over)) if env_over else base

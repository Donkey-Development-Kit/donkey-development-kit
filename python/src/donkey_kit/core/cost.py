"""Cost-attribution tags (docs/verified-apis.md §3, BG §1.7, #196).

A small, FIXED set of dimensions — ``team``, ``project``, ``env``,
``enduser.id`` — set once on the :class:`~donkey_kit.core.config.DonkeyConfig`
(and optionally overridden per run via ``donkey.run(...)``), then emitted on
every governed model call two ways:

  * as request **headers**, under UNVERIFIED placeholder names (verification discipline — the
    gateway-side cost-attribution header name is the single highest-priority
    unknown, ``docs/verified-apis.md`` §3); and
  * as ``donkey.cost.*`` **span attributes**, which carry the full value
    regardless of the header question because the SDK controls the span end to
    end.

The key set is fixed on purpose (BG §1.7 / #196 AC #1): "we don't know" is the
failure this replaces, so an unknown dimension is a configuration *error*, not
a header we silently drop. Values are length-limited and reject control
characters so a tag can never smuggle JSON or inject a second header (AC #2).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields, replace

from .errors import ConfigError

# The fixed external key names (what a user writes in ``[donkey.cost]`` toml or a
# mapping) → the Python field. ``enduser.id`` keeps the OTel-style dotted name
# as its external key; the field is the identifier-safe ``enduser_id``.
_KEY_TO_FIELD: dict[str, str] = {
    "team": "team",
    "project": "project",
    "env": "env",
    "enduser.id": "enduser_id",
}

# A tag value long enough to be a smuggled document is refused (AC #2). 256 is
# generous for team/project/env/user identifiers and well under any header-size
# limit.
_MAX_VALUE_LEN = 256


def _validate(field: str, value: str) -> None:
    """Reject a value that could not be a legitimate attribution tag: empty,
    over-long, or carrying a control character (a newline is header injection;
    any control char has no place in a dimension value). Raises
    :class:`ConfigError` naming the offending field."""
    if value == "":
        raise ConfigError(
            f"Cost tag {field!r} is empty. Omit it, or give it a value — an "
            f"empty tag is a config mistake, not 'no tag' (that is unset)."
        )
    if len(value) > _MAX_VALUE_LEN:
        raise ConfigError(
            f"Cost tag {field!r} is {len(value)} chars; the maximum is "
            f"{_MAX_VALUE_LEN}. A cost dimension is a short label, not a payload."
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ConfigError(
            f"Cost tag {field!r} contains a control character. Tags are emitted "
            f"as request headers, so a newline or control char would corrupt the "
            f"request — use a plain short label."
        )


@dataclass(frozen=True)
class CostTags:
    """The fixed four-dimension cost-attribution set (docs/verified-apis.md §3, #196).

    Every field is optional and defaults to ``None`` (unset — omitted from both
    headers and span attributes). Construction validates every set value, so an
    invalid tag is caught where it is configured, not on the first request.
    """

    team: str | None = None
    project: str | None = None
    env: str | None = None
    enduser_id: str | None = None

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if value is not None:
                _validate(f.name, value)

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
        kwargs = {_KEY_TO_FIELD[k]: (None if v is None else str(v)) for k, v in mapping.items()}
        return cls(**kwargs)

    # --------------------------------------------------------------- combine
    def merge(self, other: CostTags) -> CostTags:
        """Return ``self`` with every field ``other`` sets applied on top —
        per field, a set value in ``other`` wins, an unset one leaves ``self``
        untouched. This is the run-scope-over-config precedence: a
        ``donkey.run(project=...)`` overrides only ``project`` for its block and
        inherits the rest from the configured tags."""
        overrides = {
            f.name: getattr(other, f.name)
            for f in fields(other)
            if getattr(other, f.name) is not None
        }
        if not overrides:
            return self
        return replace(self, **overrides)

    # ------------------------------------------------------------- accessors
    @property
    def is_empty(self) -> bool:
        return all(getattr(self, f.name) is None for f in fields(self))

    def items(self) -> Iterator[tuple[str, str]]:
        """Yield ``(field_name, value)`` for each SET field, in declaration
        order — what the transport iterates to emit one header per dimension."""
        for f in fields(self):
            value = getattr(self, f.name)
            if value is not None:
                yield f.name, value

    def span_kwargs(self) -> dict[str, str]:
        """The set fields as ``build_genai_attributes`` keyword arguments
        (``team`` → ``cost_team`` …), so the span carries ``donkey.cost.*`` for
        every configured dimension."""
        return {f"cost_{name}": value for name, value in self.items()}

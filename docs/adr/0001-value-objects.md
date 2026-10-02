# ADR 0001: Value objects are frozen dataclasses; pydantic only at external-schema boundaries

- **Status:** Accepted
- **Date:** 2026-10-02
- **Issue:** #723 (part of #706). The ADR process itself (template, index,
  numbering) is #731; this record follows the shape #731 proposes (context,
  decision, consequences, status).

## Context

The contributor docs and the code disagreed about how the SDK models data:

- `CONTRIBUTING.md` §3 told contributors to use pydantic v2 idioms
  (`model_validate`, `Field`, `model_config`), and `ARCHITECTURE.md` described
  `core/` in terms of "httpx + pydantic".
- The code does something else. Every value object in `src/donkey_kit` is a
  `@dataclass(frozen=True)`: the `DonkeyConfig` family in `core/config.py`,
  the `registry/models.py` assets, `core/toolspec.py` (whose docstring says
  "no pydantic"), the `llm/catalog.py` entries, and the rest. Changes produce
  a new instance (`DonkeyConfig.with_overrides(...)`, `dataclasses.replace(...)`).
  Keyword-override bags are typed with `TypedDict` + `Unpack`
  (`core/config.py` `ConfigOverrides`), not with a model class.
- `pydantic>=2.6` is a base dependency (`python/pyproject.toml`), but the only
  module in `src/` that imports it is `provisioning/spec.py`. That module parses
  a user-authored YAML document (`DonkeySpec.from_yaml`) against a schema, and
  it belongs to the legacy provisioning half of the package (ARCHITECTURE.md,
  "Not in the stack — `provisioning/`").

The build plan (`§1.1`, "httpx + pydantic only") sets the upper bound on what
`core/` may depend on. It does not require pydantic, and `core/` imports none.

## Decision

1. **Value objects are `@dataclass(frozen=True)`.** Configuration, catalog
   entries, registry assets, tool specs, results and similar records that the
   SDK hands around or returns are frozen dataclasses. To change one, build a
   new instance (`dataclasses.replace(...)` or a typed helper such as
   `with_overrides(...)`); don't mutate it.
2. **Typed keyword bags are `TypedDict`**, used with `Unpack[...]` on the
   signature, so the overrides stay checked by `mypy --strict` without a model
   class.
3. **A plain (non-frozen) `@dataclass` is for mutable internal state only**,
   such as a private cache entry (`core/cache.py` `_Entry`) or a result being
   built up step by step (`provisioning/applier.py` `ApplyResult`). It is not
   used for a value the caller is meant to treat as immutable.
4. **pydantic is used only at an external-schema boundary**: where the SDK
   validates a document it does not control against a declared schema, and
   pydantic's validation and error reporting are the point. Today the only such
   boundary is `provisioning/spec.py`. A new pydantic model needs that
   justification in its PR.
5. **`core/` never imports pydantic.** This is stricter than the build plan's
   `§1.1` allowance and matches what the code already does.

Framework-owned pydantic objects (for example a CrewAI tool's `args_schema`, or
ADK's pydantic config) are the framework's types. An adapter can read or pass
them, and that is not a use of pydantic by the SDK under this decision.

### Alternatives considered

- **pydantic v2 models for value objects** (what the docs said). Rejected:
  nothing in `src/` outside `provisioning/spec.py` uses them, so adopting them
  would mean rewriting the existing value objects. It would also put a runtime
  dependency under every value object for validation the SDK doesn't need on
  internal data, and keep `import donkey_kit` tied to pydantic.
- **attrs, or `NamedTuple`.** Rejected: attrs adds a dependency for what the
  stdlib already covers, and `NamedTuple` is a tuple (it can be indexed and
  unpacked by position), which these records shouldn't be.

## Consequences

- `CONTRIBUTING.md` §3 and `ARCHITECTURE.md` (core in the layered stack) now
  state this convention instead of the pydantic idioms.
- `pydantic` stays a base dependency for now, because `provisioning/spec.py`
  imports it at module level and `donkey_kit.provisioning` exports
  `DonkeySpec`. Dropping it from the base dependencies and importing it lazily
  in whatever remains of provisioning is #730's job (the architecture epic);
  this ADR is the decision that work implements.
- The `pydantic.mypy` plugin stays on in `pyproject.toml` while
  `provisioning/spec.py` exists.
- The build plan's `§1.1` wording ("httpx + pydantic only") is left alone. It is
  an upper bound, and the decision here sits inside it.
- Reviewers can reject a new pydantic model outside an external-schema boundary,
  and a mutable dataclass used as a value object, by citing this ADR.

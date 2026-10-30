# ADR 0020: A public member of a stable class may be provisional, when its docstring and the snapshot both say so

- **Status:** Accepted
- **Date:** 2026-10-30
- **Issue:** #1499 (part of #1414). Amends rule 1 of
  [ADR 0006](0006-api-tiers-and-deprecation.md), as its rule 6 requires.

## Context

- ADR 0006 rule 1 makes the public methods and accessors of `Donkey` stable.
  `CONTRIBUTING.md` ("Public API surface") and
  `tests/unit/test_public_api_snapshot.py` extend that to every public member
  of `Donkey`, `ToolsFacade` and `ToolSet`, the classes a user reaches through
  `donkey` and `donkey.tools`. Rule 1's provisional tier names whole modules
  only: `donkey_kit.core.*`, the keys of each adapter's `connection_kwargs()`,
  and the `__all__` of `donkey_kit.simulator` and `donkey_kit.conformance`.
- #1446 (#1488) added `ToolsFacade.live_tools(ref)` and
  `ToolsFacade.live_card(ref)`. They let `Publication.verify()` read the live
  side of an MCP server or A2A agent without reaching into private members of
  the tools layer (§1.1). They are new in 0.2.0, sit next to blocked or newly
  verified surfaces (`Publication`, `donkey verify`), and may still change
  shape before 1.0. Their natural home is `donkey.tools`, so moving them to a
  provisional module to get the tier would put them where no user looks.
- Under rule 1 as written they are stable as soon as they ship, and rule 2
  then needs a deprecated alias for a minor release before either can change.

## Decision

1. **A public member of a stable class is provisional when both hold:**
   - its docstring contains `Provisional (ADR 0020)`, followed by what that
     means for the reader ("may change in a minor release, with a migration
     note");
   - `tests/unit/test_public_api_snapshot.py` lists it in
     `PROVISIONAL_MEMBERS`, not in `MEMBERS`.
   Such a member has the provisional tier of ADR 0006 rule 1: it is public
   and documented, and it may change in a minor release with a migration
   note, without a deprecation cycle. Every other public member of a stable
   class stays stable.
2. **The test enforces both halves.** It sorts each public member of the
   snapshotted classes by the docstring mark, and fails when that split does
   not match `MEMBERS` and `PROVISIONAL_MEMBERS`, or when a member is in both
   lists or in neither.
3. **Docs that show a provisional member say so** on the page that documents
   it.
4. **Promoting a provisional member to stable** removes the mark and moves its
   snapshot line to `MEMBERS`. It is additive and needs no ADR. **Demoting a
   stable member** is a stable-tier change: it follows ADR 0006 rule 2, as a
   removal would.
5. **The first provisional members** are `ToolsFacade.live_tools()` and
   `ToolsFacade.live_card()` (#1499).

### Alternatives considered

- **Keep them stable.** Rejected: a method added to unblock a refactor would
  be frozen before anyone outside the repo has used it.
- **Move them to a provisional module**, such as `donkey_kit.experimental` or
  a `donkey_kit.core` helper taking a `Donkey`. Rejected: users would call
  `verify_live_tools(donkey, ref)` instead of `donkey.tools.live_tools(ref)`.
  The tier would follow the import path rather than the decision, and moving
  the methods back later would break callers.
- **Prefix them with `_`.** Rejected: they would be internal, and
  `Publication.verify()` in the registry layer would be back to a private
  reach-in, which #1446 removed.
- **Mark the tier with a decorator or an attribute.** Rejected for now: the
  docstring is what `help()`, the editor and the docs show the reader, and
  the snapshot list is what review sees. A decorator would add a third place
  that has to agree with them.

## Consequences

- `test_public_api_snapshot.py` has a third hard-coded list,
  `PROVISIONAL_MEMBERS`, and its update script prints it.
- `CONTRIBUTING.md` ("Public API surface") names the exception, and
  `website/content/publishing.mdx` says the two methods are provisional.
- The provisional tier is now defined in two places, ADR 0006 rule 1 and this
  ADR. The `docs/api-stability.md` that ADR 0006 rule 5 asks for (#236) lists
  both.
- A reviewer checks a new public member of `Donkey`, `ToolsFacade` or
  `ToolSet` for its tier: a member with no mark is a stable promise.
- The planned TypeScript port needs the same per-member tier.

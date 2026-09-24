# Discovery, search & filter

Roadmap

This capability is on the [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md); the API shown here is the planned design.

`donkey.tools.discover(...)` is the one entry point for finding governed tools.
It narrows the catalog by **name/description (search)**, **governance**, domain,
tags, asset type, and environment, so an agent binds only the tools it needs
rather than the entire catalog. It returns a [`ToolSet`](https://donkey-development-kit.github.io/donkey-development-kit/tool-access/binding.md)
whose per-framework methods hand back native tool objects.

## The two most common calls

```python
# 1. Governed-only + name search:
#    only governed tools whose name or description matches the glob "*accounts*".
tools = await donkey.tools.discover(governed_only=True, search="*accounts*")

# 2. Everything governed in a domain:
tools = await donkey.tools.discover(domain="hr", governed_only=True)
```

## Filter reference

Every argument is optional and combines with `AND` semantics:

```python
tools = await donkey.tools.discover(
    search="*accounts*",     # glob over asset name + description; None = no text filter
    governed_only=True,      # True = default criteria; or an explicit GovernanceCriteria
    domain="hr",             # catalog domain
    tags=["approved"],       # all tags must be present
    asset_types=["mcp"],     # restrict to MCP servers, agents, etc.
    environment="Production",# environment-scoped governance
    limit=50,
)
```

| Argument | Type | Meaning |
|---|---|---|
| `search` | `str \| None` | Glob over asset **name and description** (`*accounts*`, `get_*`). `None` = no text filter. |
| `governed_only` | `bool \| GovernanceCriteria \| None` | `True` applies the default criteria; pass a `GovernanceCriteria` (e.g. `STRICT`) for explicit rules; `None` = unfiltered. |
| `domain` | `str \| None` | Catalog domain. |
| `tags` | `list[str] \| None` | All listed tags must be present. |
| `asset_types` | `list[AssetType] \| None` | Restrict by asset type, e.g. `["mcp"]`. |
| `environment` | `str \| None` | Which environment governance is computed against. |
| `limit` | `int` | Max results (default 50). |

  `discover(...)` is the high-level facade over `ExchangeRegistry.search()`. On
  the facade, `search` is the text/glob filter (the registry's `query`) and
  `governed_only` is the governance predicate (the registry's `governed`). The
  glob runs server-side where Exchange supports it and client-side otherwise —
  the results are identical either way.

## "Governed" is a computed predicate

Publication to Exchange says nothing about whether an asset is fronted by a
gateway, has policies applied, or passes the org's rulesets — there is no single
boolean to query. "Governed" is **computed** by joining state across systems,
and it is **environment-scoped**: an asset governed in Production may be
ungoverned in Sandbox. `GovernanceCriteria` makes every condition explicit:

```python
from donkey_kit.registry.governance import GovernanceCriteria, STRICT

@dataclass(frozen=True)
class GovernanceCriteria:
    require_api_instance: bool = True     # an API Manager instance exists in this env
    require_deployed: bool = True         # deployed to a gateway, not just configured
    require_any_policy: bool = True       # at least one policy applied
    required_policies: list[str] = ...    # e.g. ["client-id-enforcement"]
    forbidden_policies: list[str] = ...
    require_governance_pass: bool = False  # passes org rulesets with no `error` findings
    require_gateways: list[str] = ...      # only assets behind these named gateways
    require_tags: list[str] = ...
    require_lifecycle: list[str] = ...     # e.g. ["published", "approved"]
    allow_unknown: bool = False            # if a check can't be evaluated, does it pass?

# A ready-made strict preset:
STRICT = GovernanceCriteria(
    require_governance_pass=True,
    required_policies=["client-id-enforcement"],
    allow_unknown=False,
)
```

Pass it straight through:

```python
tools = await donkey.tools.discover(domain="hr", governed_only=STRICT)
```

  `allow_unknown` matters more than it looks. If the platform doesn't expose,
  say, ruleset results, then `require_governance_pass=True` with
  `allow_unknown=False` filters the whole catalog to zero — so every
  filtered-out asset carries a **reason**, surfaced by `explain()`.

## `explain()` — why a tool was included or excluded

Without it, `governed_only=True` returning an empty list is indistinguishable
from a broken credential. `explain()` reports every check:

```python
report = await donkey.registry.explain(ref, criteria=STRICT)
# GovernanceReport(governed=False, checks=[
#     Check("api_instance_exists", True,  "instance 19283 in Sandbox"),
#     Check("deployed",            True,  "gateway managed-omni-eu-1"),
#     Check("required_policies",   False, "missing: client-id-enforcement"),
#     Check("governance_pass",     None,  "UNKNOWN: rulesets API returned 403"),
# ])
```

Each check is `True` (passed), `False` (failed, with the reason), or `None`
(couldn't be evaluated — resolved via `allow_unknown`). The empty-result warning
message points you to `explain()`.

## Defaults and performance

- **Unfiltered by default.** `governed_only` defaults to `None`, and a startup
  log line states that discovery is unfiltered. Changing the default to `True`
  is reserved for a future major version.
- **Warm the index.** The governance join is a bulk operation, not one API call
  per asset. A long-running agent can build the index at startup with
  `donkey.registry.warm(environment=...)` so the first discovery is fast.

## Related

- [Framework binding](https://donkey-development-kit.github.io/donkey-development-kit/tool-access/binding.md) — turn a `ToolSet` into native tools.
- [Pinning & lockfile](https://donkey-development-kit.github.io/donkey-development-kit/tool-access/lockfile.md) — pin resolved versions for production.

# Discovery, search & filter

Status: Roadmap. Planned design; not shipped yet.

`donkey.tools.discover()` is on the [Roadmap](https://docs.donkey-kit.dev/roadmap.md); the API shown here is the
planned design. [`donkey.registry.search()`](#registry-search) is live.

`donkey.tools.discover(...)` is the one entry point for finding governed tools
to bind. It narrows the catalog by **governance**, domain and tags, so an agent
binds only the tools it needs rather than the entire catalog. It returns a
[`ToolSet`](https://docs.donkey-kit.dev/tool-access/binding.md) whose per-framework methods hand back native
tool objects. Name search and asset type are filters on the lower-level
[`donkey.registry.search()`](#registry-search).

## The two most common calls

```python
# 1. Everything governed in a domain:
tools = await donkey.tools.discover(domain="hr", governed=True)

# 2. Governed tools in a domain that carry a tag:
tools = await donkey.tools.discover(domain="hr", tags=["approved"], governed=True)
```

## Filter reference

Every argument is keyword-only and optional, and filters combine with `AND`
semantics:

```python
tools = await donkey.tools.discover(
    domain="hr",             # catalog domain
    tags=["approved"],       # all tags must be present
    governed=True,           # True = default criteria; or an explicit GovernanceCriteria
    locked=False,            # True = resolve only what donkey.lock pins
)
```

| Argument | Type | Meaning |
|---|---|---|
| `domain` | `str \| None` | Catalog domain. |
| `tags` | `list[str] \| None` | All listed tags must be present. |
| `governed` | `bool \| GovernanceCriteria \| None` | `True` applies the default criteria; pass a `GovernanceCriteria` (e.g. `STRICT`) for explicit rules; `None` = unfiltered (the default). |
| `locked` | `bool` | `True` resolves only the versions pinned in `donkey.lock` (see [Pinning & lockfile](https://docs.donkey-kit.dev/tool-access/lockfile.md)). Default `False`. |

## Registry search

`donkey.registry.search()` is Live. `discover(...)`
is the high-level facade over the registry. When you need name search, asset
types or a result limit, call `donkey.registry.search()` directly. It returns
`AssetRef` handles rather than a bindable `ToolSet`:

```python
refs = await donkey.registry.search(
    query="*accounts*",       # a glob over asset ID, name and description; None = no text filter
    asset_types=["mcp"],      # "mcp", "agent" or "api"
    tags=["approved"],        # all tags must be present
    limit=50,                 # max results (default 50)
)
for ref in refs:
    print(ref.coordinates, ref.name, ref.type, ref.tags)
```

It searches the business group in `ANYPOINT_ORG_ID` and returns each asset's
latest version:

- A `query` without `*`, `?` or `[` is matched by Exchange: whole words and
  word prefixes of the asset ID, name or description, ignoring case. `"sku"`
  and `"look"` find `SKU Lookup MCP Server`; `"ku-loo"` does not.
- A `query` with `*`, `?` or `[` is a glob, matched by the SDK against the
  asset ID, name and description, ignoring case. Exchange does not match
  inside words, so a glob query reads the group's assets that pass the other
  filters, 250 at a time, until it has `limit` matches.
- `asset_types` and `tags` are applied by Exchange. `"api"` covers Exchange's
  REST, HTTP and SOAP API types. A tag is an Exchange tag.
- `domain`, `environment`, `governed` and the `"a2a-agent"` type raise
  `NotImplementedError` for now. Exchange has no domain filter, and
  `environment` and `governed` need the governed-state join below.

## "Governed" is a computed predicate

Publication to Exchange says nothing about whether an asset is fronted by a
gateway, has policies applied, or passes the org's rulesets — there is no single
boolean to query. "Governed" is **computed** by joining state across systems,
and it is **environment-scoped**: an asset governed in Production may be
ungoverned in Sandbox. `GovernanceCriteria` makes every condition explicit. It
and `STRICT` are in `donkey_kit.experimental` until discovery is verified:

```python
from donkey_kit.experimental import STRICT, GovernanceCriteria

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
tools = await donkey.tools.discover(domain="hr", governed=STRICT)
```

  `allow_unknown` matters more than it looks. If the platform doesn't expose,
  say, ruleset results, then `require_governance_pass=True` with
  `allow_unknown=False` filters the whole catalog to zero — so every
  filtered-out asset carries a **reason**, surfaced by `explain()`.

## `explain()` — why a tool was included or excluded

Without it, `governed=True` returning an empty list is indistinguishable
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

- **Unfiltered by default.** `governed` defaults to `None`, and a startup
  log line states that discovery is unfiltered. Changing the default to `True`
  is reserved for a future major version.
- **Listed is not bindable.** Unfiltered discovery still lists a server the
  gateway does not govern, and attaches its `explain()` report to the handle
  (`McpServerHandle.governance`). Binding refuses it with
  `UngovernedMcpServer` (see [Only governed servers are bound](https://docs.donkey-kit.dev/tool-access/binding.md#only-governed-servers-are-bound)).
  Pass `governed=True` to get a set you can bind as a whole.
- **Warm the index.** The governance join is a bulk operation, not one API call
  per asset. A long-running agent can build the index at startup with
  `donkey.registry.warm(environment=...)` so the first discovery is fast.

## Related

- [Framework binding](https://docs.donkey-kit.dev/tool-access/binding.md) — turn a `ToolSet` into native tools.
- [Pinning & lockfile](https://docs.donkey-kit.dev/tool-access/lockfile.md) — pin resolved versions for production.

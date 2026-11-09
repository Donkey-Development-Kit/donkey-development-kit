# Pinning & lockfile

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

A platform team can change the tools your agent binds without touching your
code. They can edit a tool's schema or description, add a Tool Mapping rule
that renames it, or turn on Progressive Disclosure. The lockfile records what
each governed MCP server listed when you locked it. A later
`discover(locked=True)` refuses to bind when the gateway lists something
different.

  The lock **detects drift; it does not control access.** The gateway's own
  policies still decide what a caller may list and call. A tool the lock pins
  is not thereby allowed, and a tool missing from the listing can still be
  callable. Under Tool Mapping, for example, the upstream name keeps working
  after the mapped name replaces it in `tools/list`.

`donkey.tools.lock()`, `discover(locked=True)` and `donkey_kit.ToolLockDrift`
are **Provisional**: they may change in a minor release, with a migration note.

## Write the lock

`donkey.tools.lock()` discovers servers the way
[`discover()`](https://docs.donkey-kit.dev/tool-access/discovery.md) does. It then sends one `tools/list` to
each server, with the credential a binding would send, and writes
`./donkey.lock`:

```python
import asyncio
from donkey_kit import Donkey

async def main() -> None:
    async with Donkey.from_env() as donkey:
        path = await donkey.tools.lock(tags=["inventory"])
        print(path)  # the absolute path written

asyncio.run(main())
```

It takes the same `tags=`, `governed=` and `auth=` as `discover()`, plus
`path=` to write somewhere other than `./donkey.lock`. It locks the whole
listing, so lock first and narrow with `filter()` afterwards. If you pass
`governed=None` and the set contains an ungoverned server, it still raises
`UngovernedMcpServer`. If discovery finds no servers at all, `lock()` raises
`ConfigError` and writes nothing, rather than a lock that would bind nothing.
Run `lock()` as the identity your agent runs as, not inside
`donkey.as_user()`; see the note under [Check against the lock](#check-against-the-lock).

## What the lock records

The file is JSON with sorted keys, so re-locking an unchanged catalog writes
the same bytes:

```json
{
  "digest": "sha256",
  "servers": {
    "com.example/inventory": {
      "asset_id": "inventory",
      "group_id": "com.example",
      "tools": {
        "get_stock": "3f1c…",
        "reserve_item": "9a07…"
      },
      "version": "1.0.0"
    }
  },
  "version": 1
}
```

- Each server is keyed `group_id/asset_id`, so a new version that lists the
  same tools is not drift.
- Each tool is keyed by the name **the gateway lists**, never the upstream
  name. Under Tool Mapping, that is the mapped name.
- Under Progressive Disclosure, the gateway lists `search_tools` and
  `invoke_tool`, and those two are what the lock pins. The lock never calls
  `search_tools` to pin the tools a search would return.
- Each digest is the SHA-256 of the tool's `name`, `title`, `description`,
  `inputSchema`, `outputSchema` and `annotations` (those present), serialised
  as JSON with sorted keys. The gateway does not return descriptor keys in a
  stable order, so a change of key order does not count as a change. Other
  fields, such as `_meta`, are left out, so that a new field in the MCP
  library does not read as a change to every tool.

Commit `donkey.lock` with your agent code. When someone re-locks, a catalog
change then shows up as a diff in a pull request.

## Check against the lock

```python
from donkey_kit import ToolLockDrift

tools = await donkey.tools.discover(tags=["inventory"], locked=True)
try:
    agent_tools = await tools.langgraph()
except ToolLockDrift as exc:
    for d in exc.drift:
        print(d.server, d.tool, d.kind)
    raise
```

`discover(locked=True)` reads `./donkey.lock`, or the file you pass as
`lock_path=`. Pass it the same `tags=` and `governed=` you locked with: a
pinned server that this discovery does not return, because it is outside the
tags or no longer governed, counts as missing. Narrowing the set afterwards
with `filter()` is not drift. The check runs when the set first lists its tools. Every binding
lists them before it builds anything, so a set that has drifted never reaches
your framework. `await tools.describe()` runs the same check without binding.

Locked per-tool bindings (`langgraph()`, `llamaindex()` and `anthropic()`)
build native tools from an owned copy of the validated descriptor snapshot,
not a second listing. Their names and metadata stay with that snapshot through
invocation; the gateway still decides whether each call is allowed.

Whole-server bindings (ADK, Strands, CrewAI, Agent Framework and OpenAI Agents)
instead use the framework's later listing, narrowed by tool name. That later
listing is not re-checked against the lock, so these bindings do not provide
the same metadata snapshot guarantee.

| What changed at the gateway | Result |
| --- | --- |
| A pinned server is no longer discovered | `ToolLockDrift`, `kind="server_missing"`, raised by `discover()` before any connection |
| A pinned tool is no longer listed (removed, or renamed by Tool Mapping) | `ToolLockDrift`, `kind="tool_missing"` |
| A pinned tool's descriptor changed | `ToolLockDrift`, `kind="changed"` |
| The gateway lists a tool the lock does not pin | Not drift: the tool is dropped, logged as a warning, and listed in `ToolSet.excluded` with `reason="unpinned"` |
| Discovery finds a server the lock does not pin | Not drift: the server is dropped, with a warning |
| `donkey.lock` is missing, not valid JSON, or in a format this SDK does not read | `ConfigError`, before any discovery |

`ToolLockDrift.drift` is a tuple of `LockDrift(server, tool, kind)` entries,
one for each difference, and the message names every one of them. To accept a
change, review it, then run `donkey.tools.lock()` again.

  The check compares what **this caller** is listed. A gateway rule can hide a
  pinned tool from one caller, for example a per-user token sent through
  `donkey.as_user()`, or an access rule scoped to one client. That caller gets
  `ToolLockDrift` with `kind="tool_missing"`. Lock with the identity your
  agent runs as.

## Test drift offline

Inside a [`donkey.simulate_mcp()`](https://docs.donkey-kit.dev/simulator.md#mcp-tool-fault-injection)
block, `discover(locked=True)` checks the declared servers against the lock
with the same rules as the table above. Declare the servers your lock pins,
then change a descriptor or leave a server out to make the test raise
`ToolLockDrift`. `donkey.tools.lock(path=...)` inside the block writes a lock
of the declared servers. The lockfile still has to exist: without one,
`discover(locked=True)` raises `ConfigError`, as it does outside the block.

## Verification status

The lock's behaviour is tested offline, against `tools/list` replies recorded
from a governed gateway with Tool Mapping or Progressive Disclosure applied.
The facts it relies on were observed live:

- the names the gateway lists
- the unstable key order in descriptors
- what Progressive Disclosure lists

No live gateway has recorded a full `lock()`, then `discover(locked=True)`,
round trip yet. That is why this page is marked Offline-verified. The
[MCP gateway policy inventory](https://docs.donkey-kit.dev/tool-access/gateway-policies.md) describes the
policies that change a server's listing.

## Registry caching

Registry lookups are cached with a configurable TTL (default 300 seconds).
These are `ExchangeRegistry.search()`, `resolve_mcp()`, `explain()` and
`resolve_agent()`; see [Discovery, search & filter](https://docs.donkey-kit.dev/tool-access/discovery.md).

```python
from donkey_kit import Donkey

donkey = Donkey.from_env(registry_cache_ttl_s=60)
```

- `donkey.registry.refresh()` invalidates the cache and re-fetches on next
  use. Call it after a platform team publishes a change you need to see
  immediately. `donkey.tools.refresh()` does the same and also drops the cached
  discoveries. `donkey.tools.warm()` drops the registry cache before it reads.
- Pass `fresh=True` to `search()`, `explain()` or `resolve_mcp()` to read past
  the cache for that one lookup and refill its entry. A `discover()` whose
  cached result expired does this for every lookup it makes.
- Set `DONKEY_REGISTRY_CACHE_TTL_S` to change the TTL from the environment.
- Set `DONKEY_NO_CACHE=1` to bypass the cache entirely, for debugging a
  discovery result that looks stale.

Caching and pinning solve different problems. Caching controls how often you
ask the registry the same question again. The lock controls whether the tools
you bind may change under a running agent at all.

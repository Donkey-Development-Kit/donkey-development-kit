# Pinning & lockfile

Roadmap

This capability is on the [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md); the API shown here is the planned design.

Governed tool catalogs change under you. A platform team edits an MCP server's
tool schema, bumps a policy, or republishes an asset — and if your agent
resolves `version="latest"` at startup, that change silently alters agent
behaviour in production. Pinning and the lockfile keep what your agent binds
under version control.

## Pin by default

`donkey.tools.discover()` requires an explicit version on every asset reference
by default. `version="latest"` is allowed, but logs a warning — it is an opt-in
escape hatch, not the default path:

```python
# explicit — no warning
tools = await donkey.tools.discover(domain="hr", version="1.2.0")

# opt-in — logs a warning every time it resolves
tools = await donkey.tools.discover(domain="hr", version="latest")
```

## The lockfile

`donkey.tools.lock()` resolves the current discovery call and writes a
`donkey.lock` file recording the resolved versions and content digests of every
asset it touched:

```bash
python -c "import asyncio; from donkey_kit import Donkey; asyncio.run(Donkey.from_env().tools.lock())"
```

```yaml
# donkey.lock (illustrative)
lockedAt: 2026-08-28T00:00:00Z
assets:
  - ref: com.acme/hr-tools-mcp/1.2.0
    digest: sha256:...
  - ref: com.acme/vendor-shipment-mcp/1.0.0
    digest: sha256:...
```

Once a `donkey.lock` exists, pass `locked=True` and discovery refuses to resolve
anything not already in the lockfile:

```python
tools = await donkey.tools.discover(domain="hr", locked=True)
# raises if discovery would resolve an asset/version not in donkey.lock
```

  Commit `donkey.lock` alongside your agent code and treat it as a required step
  before deploying. A version bump then becomes a reviewable diff in a pull
  request instead of a runtime surprise.

## Registry caching

Registry lookups (`ExchangeRegistry.search()`, `resolve_mcp()`,
`resolve_agent()` — see [Discovery, search & filter](https://donkey-development-kit.github.io/donkey-development-kit/tool-access/discovery.md))
are cached with a configurable TTL (default 300 seconds):

```python
donkey = Donkey.from_env(registry_cache_ttl_s=300)
```

- `donkey.registry.refresh()` invalidates the cache and re-fetches on next use —
  call it after a platform team publishes a change you need to see immediately.
- Set `DONKEY_REGISTRY_CACHE_TTL_S` to change the TTL from the environment.
- Set `DONKEY_NO_CACHE=1` to bypass the cache entirely, for debugging a
  discovery result that looks stale.

Caching and pinning solve different problems: caching controls how often you
re-ask the registry the same question; pinning controls whether an answer is
allowed to change under a running agent at all.

# ADR 0024: The tool lockfile pins gateway-visible tool descriptors and detects drift; it never controls access

- **Status:** Accepted
- **Date:** 2026-11-07
- **Issue:** #1646 (BG §2.7). The wire contract it rests on was settled in
  #1642 (`docs/verified-apis.md` §9, "Lockfile contract under Progressive
  Disclosure and Tool Mapping").

## Context

`donkey.tools.lock()` and `discover(locked=True)` raised
`_verify.blocked(...)` until the gateway contract was known. #1642 probed it
live and found three facts the design has to follow:

- The gateway serialises a tool descriptor's keys in a different order from
  one session to the next, so a hash of raw bytes is not stable.
- Tool Mapping renames a tool in `tools/list` but leaves the upstream name
  callable, and the gateway does not expose the mapping table.
- Progressive Disclosure lists only `search_tools` and `invoke_tool`. The real
  tools are reachable only through a query.

The build plan's *Do not build* list rules out client-side policy enforcement
and a second, client-side tool search.

## Decision

1. The lock pins what `tools/list` returns, keyed by the gateway-visible name.
   An upstream name is never recorded. Under Progressive Disclosure, the lock
   pins `search_tools` and `invoke_tool` and never calls `search_tools`.
2. Each tool's digest is the SHA-256 of its descriptor restricted to `name`,
   `title`, `description`, `inputSchema`, `outputSchema` and `annotations`,
   serialised as JSON with sorted keys, compact separators and
   `ensure_ascii=False` (`tools/lockfile.py`, `digest`). Nothing hashes
   unsorted JSON. Other fields are left out so that a field the `mcp` library
   adds or defaults does not change every digest.
3. The file is `./donkey.lock` by default. It is JSON with sorted keys, with
   `"version": 1` and `"digest": "sha256"` at the top level, and it keys
   servers by `group_id/asset_id`, so a version bump that lists the same tools
   is not drift. `read_lock` raises `ConfigError` for any other version or
   digest, and for a missing, undecodable or malformed file. `lock()` refuses
   to write a lock with no servers (`ConfigError`).
4. These are drift, and raise `ToolLockDrift` before any tool binds:
   - a pinned server that is not discovered (raised by `discover()`, with no
     connection made)
   - a pinned tool that is not listed (a removal, or a rename)
   - a changed digest

   A tool or server the lock does not pin is not drift. It is dropped, logged
   as a warning, and reported in `ToolSet.excluded` with `reason="unpinned"`.
5. The check compares what the calling identity is listed. A pinned tool that
   one caller is not listed is drift for that caller. Server presence is
   checked once, by `discover()`; narrowing a locked set with `filter()` is
   not drift.
6. The lock is a drift detector, never access control. The SDK does not refuse
   a call by name because it is unpinned or upstream; the gateway's policies
   enforce.
7. `lock()`, `discover(locked=True)`, `lock_path=`, `ToolLockDrift` and the file
   format are Provisional (ADR 0020).

Rules 1 to 5 are enforced by `tests/unit/test_lockfile.py`,
`test_toolset_lock.py` and `test_discover_locked.py`. Rule 6 is enforced by
review. Rule 7 is enforced by the public API snapshot.

### Alternatives considered

- **Pin the upstream names, or the tools behind `search_tools`.** Rejected: the
  gateway does not expose the mapping, and pinning search results would mean
  building a client-side discovery protocol.
- **Hash the raw `tools/list` bytes.** Rejected: raw key order is unstable, as
  #1642 observed live.
- **Hash the whole descriptor.** Rejected: the SDK reads descriptors through
  the `mcp` library's model, and a newer library can add default-valued fields,
  so one environment's lock would drift in another with no gateway change.
- **Treat an unpinned tool as drift.** Rejected: a server that adds a tool would
  then break every locked agent. Dropping the tool keeps the bound set to what
  was reviewed, and the warning surfaces the change.
- **Key servers by full coordinates, including the version.** Rejected: a
  version bump would read as a missing server plus a new one, rather than as a
  comparison of the tools.

## Consequences

- The `blocked_lock()` guard is gone, and the lockfile is no longer on the
  "still blocked" lists in `README.md`, `ARCHITECTURE.md` or
  `docs/verified-apis.md`.
- The acceptance repo's blocked-surface tests for `lock()` and
  `discover(locked=True)` have to change (a follow-up issue there).
- Changing the file format means bumping `LOCK_VERSION` and adding a migration
  note. An older SDK refuses the newer file with `ConfigError` rather than
  misreading it.
- A full live `lock()` then `discover(locked=True)` round trip is not recorded
  yet. The behaviour is tested against recorded `tools/list` catalogs.

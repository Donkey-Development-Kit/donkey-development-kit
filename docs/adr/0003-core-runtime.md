# ADR 0003: A core `Runtime` owns the governed wiring, with one process default

- **Status:** Accepted
- **Date:** 2026-10-03
- **Issue:** #725 (part of #707), merged as #843. Recorded after the fact under
  the ADR process from #731.

## Context

Every governed surface ships in three forms that must behave the same
(`ARCHITECTURE.md`, "How the pieces connect"): `donkey.<framework>.<factory>()`,
`connection_kwargs()`, and a module-level factory such as
`donkey_kit.integrations.langgraph.chat_model()`. The module-level factories
were documented as equivalent to `Donkey.from_env().<framework>.<factory>()`,
but they weren't. The layered import contract (`§1.1`) forbids
`integrations` from importing the top package, so `integrations/_base.py`
built its own client for each adapter class. That client had no budget, no
data-plane token auth or 401 refresh, no OTLP export, and a connection pool
that was never closed (evidence in #725).

The fix had to give the module-level factories the same wiring `Donkey` gets
without breaking the layering.

## Decision

1. **`core/runtime.py` `Runtime` owns what a governed handle needs**: the
   resolved `DonkeyConfig`, the OTLP export bootstrap
   (`configure_otlp_export`), the control-plane auth provider, the data-plane
   credential (`llm_auth`, used in the `jwt` and `bearer` modes), one `Budget`,
   one shared client per credential plane, the blocking client built on first
   use, and the close of all of them (`aclose()`, `close()`).
2. **`Runtime` is the only place shared clients are built.** No
   `build_http_client(` call exists outside `core/runtime.py` (apart from its
   definition in `core/transport.py`).
   `tests/unit/test_default_runtime.py`
   `test_no_http_client_is_built_outside_the_runtime` holds this. The rule
   covers the shared clients only: an adapter or `LLMClient` used without a
   runtime's blocking-client accessor builds its own blocking client on
   demand (`_own_sync_client`, through `build_sync_http_client`). Nothing
   closes that client today, and the test doesn't look for it (tracked as
   #949).
3. **`Donkey` wraps one `Runtime`.** `Donkey.__init__` builds it, and the
   handle's config, auth, budget and clients are the runtime's.
4. **`core.runtime.default()` is the process-wide runtime** behind the
   module-level factories. It is built lazily from the environment, exactly as
   `Donkey.from_env()` would be. It is guarded by a lock, so concurrent first
   calls get one instance. It is closed at interpreter exit (`atexit` runs
   `close_default()`). `integrations/_base.py` `default_adapter()` builds each
   module-level adapter on it and rebuilds when the default has been closed
   and replaced.
5. **It lives in `core`** so `integrations` can reach it by importing
   downward. The layering contract is unchanged.

### Alternatives considered

- **Let `integrations` import `Donkey`.** Rejected: it breaks the layered
  contract, and every adapter would then depend on the top-level orchestrator.
- **Keep per-adapter clients and copy the wiring into `_base.py`.** Rejected:
  a second copy of the budget, auth and OTLP wiring would drift from
  `Donkey.__init__`, which is how the original gap arose.
- **Drop the module-level factories.** Rejected: they are one of the three
  documented forms, and removing them is a breaking change for a gap that can
  be closed.

## Consequences

- Every module-level factory shares one client with `Donkey.from_env()`-equivalent
  budget, auth and OTLP behaviour, sends the same headers and returns the same
  `connection_kwargs()`. `tests/unit/test_default_runtime.py` checks this
  for every entry in `ADAPTERS`, and checks that the runtime is closed at
  interpreter exit.
- The process default is built from the environment only, with no `llm_auth`
  provider. A caller who needs non-env configuration, lifecycle control or a
  data-plane token provider (`jwt` or `bearer` mode) uses an explicit `Donkey`,
  as the `default_adapter()` docstring says.
- `close_default()` called from inside a running event loop can close only the
  blocking client; the caller awaits `default().aclose()` in that case.
- The default runtime is process-global state that the SDK creates on first
  use of a module-level factory. It is SDK-owned state, created only when the
  user calls a module-level factory, so it is not the kind of side effect ADR
  0010 forbids. The OTLP bootstrap it runs, like every `Runtime`, is, and ADR
  0010 changes that bootstrap, not this ADR.

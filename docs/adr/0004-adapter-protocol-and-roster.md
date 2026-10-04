# ADR 0004: The adapter contract is checked, capabilities are frozen per factory, and `ADAPTERS` is the only roster

- **Status:** Proposed
- **Date:** 2026-10-03
- **Issue:** #726 (part of #707). Recorded under the ADR process from #731.

## Context

Each framework adapter is a subclass of `integrations/_base.py` `Adapter`, found
by `Donkey.__getattr__` through the `ADAPTERS` registry of `AdapterSpec`
entries in `integrations/__init__.py`. Part of the contract is now explicit:
`Adapter` is an ABC with an abstract `connection_kwargs()`, `Adapter.extra` is
read from `ADAPTERS`, and the `last_call` capability is a class attribute
(`observes_last_call`) with per-factory overrides
(`factory_observes_last_call`) that are never changed on an instance (#741).
Validation is shared too: every adapter's `connection_kwargs()` reaches
`Adapter._openai_connection()`, which runs `Adapter._require_proxy()`
(`cfg.validated(need="llm")`, then a `ConfigError` in a token auth mode when
the shared client has no token provider, #828, #836). What #726 found that is
still open:

- Capabilities are spread over class attributes (`observes_last_call`,
  `factory_observes_last_call`, `AdapterSpec.conformance_tested`) rather than
  one value per factory, and the sync and streaming support of each factory
  isn't declared anywhere.
- The roster is declared in about ten places: `ADAPTERS`, the `Donkey`
  annotations in `donkey.py`, the extras in `python/pyproject.toml`, the
  `integrations are mutually independent` import-linter contract, the mypy
  overrides, `scripts/verify_frameworks.py`, `tests/conformance/suite.py`,
  `.github/workflows/nightly-matrix.yml` and the docs. Only
  `tests/unit/test_verify_frameworks.py` cross-checks any of them, and only
  against the extras.
- The three ergonomic forms are held by hand-written tests
  (`tests/unit/test_adapter_ergonomics.py`, whose `_FACTORIES` table a new
  adapter must be added to by hand).

## Decision

1. **The adapter contract is a typed Protocol**, `AdapterProtocol`, with
   `connection_kwargs() -> Mapping[str, Any]`. `Adapter` implements it, and
   mypy checks every adapter against it.
2. **Capabilities are a frozen value per factory**:
   `AdapterCapabilities(transport, sync, streaming, typed_refusals,
   observes_last_call)`, a frozen dataclass (ADR 0001). A factory's
   capabilities never change at runtime, so ADK's `model()` and `gemini()`
   report separately.
3. **Validation runs once, in one base-class method.** It runs
   `cfg.validated(...)` and the token-mode and sync guards, and every
   `connection_kwargs()` goes through it, so each adapter raises `ConfigError`
   in `jwt` mode without a provider, as `donkey.llm.client()` does.
   `Adapter._require_proxy()` already does this for the proxy guards; #726
   names it (#726 proposes `Adapter._connection()`), adds the sync guard that
   `llm/client.py` applies (the token modes are async-only, so a blocking
   client in one raises `sync_token_auth_error`), and
   adds a test parametrised over `ADAPTERS` so a new adapter can't skip it.
4. **`ADAPTERS` is the only roster.** `Adapter.extra` and the `Donkey`
   attribute annotations come from it. A roster-consistency test, run in CI,
   checks that it matches the pyproject extras, the modules in the
   independence contract, the `KNOWN_LIMITATIONS` keys and the nightly matrix.
   Places that can't be generated from it are held by that test.
5. **Third-party adapters registered through entry points are out of scope.**
   That needs its own ADR, when there is demand for it.

### Alternatives considered

- **Keep `Adapter` as an ABC only.** Rejected as the whole answer: an ABC
  checks that a method exists, not the shape of the kwargs bag, and it can't
  cover the module-level factories. The ABC can stay as the shared
  implementation.
- **Generate every roster place from `ADAPTERS` at build time.** Rejected:
  `pyproject.toml`, the import-linter contract and the workflow YAML are static
  files read by other tools. A test that fails on drift is simpler and keeps
  those files readable.
- **Register adapters through entry points now.** Deferred (rule 5): one
  in-tree roster is enough for eight adapters, and a plugin surface would add
  a public API for no current user.

## Consequences

- Adding a framework means one `ADAPTERS` entry, its module, and whatever the
  consistency test then asks for. The test names each place that is missing.
- Changing the independence contract is also an import-linter change, so it
  needs an ADR under the process in `docs/adr/README.md`.
- ADR 0002's adapter-declared error translators hang off the same `AdapterSpec`
  entry.
- `ARCHITECTURE.md` ("How the pieces connect", the three-forms paragraph) and
  `CONTRIBUTING.md` §3 (the rule-to-enforcement map) name the new tests when
  #726 lands.

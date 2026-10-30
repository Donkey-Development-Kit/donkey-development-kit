# ADR 0012: Customer-registered refusal shapes, in one process-wide registry behind a hook slot

- **Status:** Accepted
- **Date:** 2026-10-15
- **Issue:** #210 (BG §2.2).

## Context

`classify()` in `core/errors.py` maps the gateway's rejection responses to typed
exceptions. `ARCHITECTURE.md` (*Error-taxonomy design*) states that it is
fixture-driven: every rule comes from a real capture against a live proxy,
never from a guess (§0.3). That covers the Omni Gateway's own policies.

A customer's own gateway policy, such as a PDK policy, sends a refusal shape
that no capture of ours can contain. Today it lands on `UpstreamRequestError`
(a nested error object, read as a provider mistake) or on the generic
`PolicyViolation` with `policy="unknown"`. The customer's code cannot branch on
it, a `donkey.on(...)` handler cannot target it, and the span cannot name it.

`classify()` also runs where no `Donkey` exists: in the transport, in the
typed-refusal bridge (`core/refusals.py`, ADR 0002) when it classifies a
framework's status error, and in `donkey doctor`. A per-`Donkey` registry would
classify the same response differently on those paths.

`errors.py` sits below most of `core` in the "layers inside core" contract and
is over its size budget (`_OVERSIZED_CORE_MODULES`), so the registry cannot live
in it.

## Decision

1. **A customer may register a refusal shape.** `classify.register(match=...,
   raises=...)` maps one response shape to a `PolicyViolation` subclass the
   customer defines. The built-in rules stay fixture-driven; a registered shape
   is the customer's own statement about their own policy, and the SDK never
   ships one.
2. **A built-in shape always wins.** `classify()` tries every built-in policy
   rule first, then the registry, then the upstream-envelope and unconfirmed
   fall-through rules (`_MATCHERS` in `core/errors.py`). `register()` builds the
   minimal response the spec describes and runs it through the built-in rules;
   a spec one of them claims is rejected by name, as are 401, 429, non-4xx
   statuses and specs whose only discriminator is a captured provider error
   value. Every rule is checked at `register()` time, never at the first
   refusal. Enforced by `tests/unit/test_classifier.py`.
3. **The registry is process-wide.** `donkey_kit.classify` and `Donkey.classify`
   are one `Classifier` instance, so every classification path agrees, including
   clients built before a registration. The registry is an immutable tuple
   replaced on each change, so classification takes no lock. It changes only
   when the customer calls `register()` or `unregister()`, never as a side
   effect of building an object, so it is consistent with ADR 0010.
4. **`errors` reaches the registry through a hook slot, never an import.**
   `core/classifier.py` fills `errors._REGISTERED_MATCHER` when it is imported.
   It sits in the "layers inside core" contract beside `cost` and `reactions`,
   above `_policy_spec` (the match-spec checks), which sits above `errors`.
   Enforced by `lint-imports`.
5. **Tests isolate the registry.** `donkey_kit._testing.isolated_classify()`
   snapshots and restores it. The conformance plugin's `donkey` and
   `donkey_classify_registry` fixtures use it (the second was named
   `classify_registry` until #1425 renamed it before it shipped), and this
   repo's `tests/conftest.py` empties it around every test.
6. **A registered slug is recorded verbatim.** The class's `policy` (or the
   `error_type` / `error_code` value) is `donkey.policy.type` on the span. It
   may not be a built-in slug or `.policy` value, and one slug names one class.

### Alternatives considered

- **A registry per `Donkey`.** Rejected: `classify()` runs without a `Donkey`,
  so the bridge and `donkey doctor` would type the same response differently
  from the transport.
- **Let a registration override a built-in shape.** Rejected: a verified shape
  is a fact about the gateway. Overriding it would let a typo retype PII or
  kill-switch refusals, and `AgentKilled` must stay terminal.
- **Settle a tie by registration order.** Rejected: the result would depend on
  import order. A tie becomes the generic `PolicyViolation`, whose remediation
  names the competing registrations, and is logged once.
- **Put the registry in `errors.py`.** Rejected: the module is over its size
  budget and may only shrink. The hook slot keeps `errors` free of the registry.

## Consequences

- `core/classifier.py` and `core/_policy_spec.py` are new core modules, placed
  in the "layers inside core" contract. `classify` moves from `core.errors` to
  `core.classifier` in the `donkey_kit` and `donkey_kit.core` exports; the
  function in `core.errors` keeps its behaviour.
- `ARCHITECTURE.md` (*Layered architecture* and *Error-taxonomy design*) and the
  docs site (`custom-policies.mdx`, plus `errors`, `refusal-handlers`,
  `telemetry` and `testing`) describe the registry.
- `donkey.simulate()` and the conformance harness serve a registered type's own
  minimal response, through `simulator.inject.resolve_fixture()`. The `gateway`
  fixture and `donkey mock` run in another process and serve only built-in shapes.
- CrewAI's provider owns its transport, so a registered type is neither raised
  nor handled for its calls. `KNOWN_LIMITATIONS` and the README exemptions
  table record it under `custom_policy_typed`.
- A test that registers a type outside the fixtures leaks into later tests in
  the same process. The fixtures and `isolated_classify()` are the documented
  way to avoid it.

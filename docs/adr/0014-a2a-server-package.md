# ADR 0014: `donkey.serve()` wraps the official a2a-sdk, in a top-level `a2a` package beside `integrations`

- **Status:** Accepted (2026-10-29, #1425, so `donkey_kit.a2a` and the
  `A2ATask`/`A2AResult` exports ship in 0.2.0 on a settled decision)
- **Date:** 2026-10-24
- **Issue:** #224 (BG §2.9.1). Governed inbound is #225, `donkey expose` #226,
  `donkey dev` #227.

## Context

BG §2.9.1 asks for an A2A listener in one line: `donkey.serve(handler)`. Today,
accepting a task from a partner agent means writing a server and an agent card
by hand, adding logging and tracing, and then getting a certificate, opening a
port and registering the agent.

The A2A protocol already has an official Python implementation, the `a2a-sdk`.
It provides a request handler, a task store, the task lifecycle and the
Starlette routes. The build plan's *Do not build* list rules out a home-grown
protocol implementation. DDK's value is what a bare `a2a-sdk` server lacks:

- an agent card generated from the `@donkey.agent` record that `donkey scan`
  also reads (#217), so the two cannot drift;
- a localhost bind by default, so the gateway is the public face;
- (#225) inbound correlation, cost tags, tracing and typed refusals.

The `layered architecture (§1.1)` contract in `python/pyproject.toml` is
`exhaustive`, so a new top-level module must be placed in it. `integrations/`
holds one adapter per agent framework, each returning native framework objects
(ADR 0004). An A2A server is not a framework adapter: it is not in the
`ADAPTERS` roster and returns no framework object.

## Decision

1. **The protocol is the `a2a-sdk`'s, never DDK's.** `donkey_kit.a2a` wraps
   `AgentExecutor`, `DefaultRequestHandler`, `InMemoryTaskStore`,
   `TaskUpdater`, `create_agent_card_routes`, `create_jsonrpc_routes` and
   `AgentCard`, and reimplements none of them. Every wrapped name is a
   `docs/verified-apis.md` §8.5 row. Enforced in review.
2. **The card comes only from the `@donkey.agent` record.** Its name,
   description and skills are the `AgentSpec`'s name, docstring and skills.
   An unmarked handler raises `TypeError`. Enforced by
   `tests/unit/test_a2a_serve.py`.
3. **`donkey.serve()` binds to `127.0.0.1` by default.** Binding elsewhere
   takes an explicit `host=`. Enforced by `tests/unit/test_a2a_serve.py`.
4. **`a2a` is a top-level package and an independent sibling of
   `integrations`** (`"integrations | a2a"` in the layers contract). Neither
   may import the other. `core` must never import `a2a` (the core
   `forbidden` contract). Enforced by `lint-imports`.
5. **The `a2a-sdk` loads only when a server is built.** It is the `[a2a]`
   extra (`a2a-sdk[http-server]`, `uvicorn`), imported inside `build_app()`
   and `serve()`. `import donkey_kit` and `import donkey_kit.a2a` never load
   it. A missing extra raises an `ImportError` that names
   `pip install "donkey-kit[a2a]"`. The extra may bring pydantic and protobuf,
   which `core` forbids; that is allowed because nothing below `a2a` imports
   them. Enforced by `tests/unit/test_a2a_serve.py`.
6. **A handler failure fails the task, never the server.** The caller sees
   only the exception's type name; the message goes to the
   `donkey_kit.a2a._server` logger. Enforced by `tests/unit/test_a2a_serve.py`.

### Alternatives considered

- **Have users call the `a2a-sdk` directly.** Rejected: they get a bare
  server, with a hand-written card that can drift from the `@donkey.agent`
  marker, anonymous inbound tasks and a bind address left to them. The
  differentiator is governed inbound and one-command exposure, not the
  protocol.
- **Put the server in `integrations/a2a.py`.** Rejected: it would sit in the
  adapter roster's package without being an adapter, and the
  `integrations are mutually independent` contract and the `ADAPTERS` roster
  tests assume every module there is one.
- **Implement the A2A protocol in DDK.** Rejected by the build plan's *Do not
  build* list: the `a2a-sdk` tracks the spec, and a second implementation
  would drift from it.

## Consequences

- `python/pyproject.toml` gains the `[a2a]` extra (also in `[all]`), the
  layers placement, the core `forbidden` entry and the mypy `a2a.*` skip.
  `dependency_allowlist.toml` gains `a2a-sdk`.
- The PR `test` matrix installs `[a2a]`, so the round-trip tests run there; the
  base-only and typecheck jobs skip them through `importorskip`.
- `docs/verified-apis.md` §8.3 records the `a2a-sdk>=1.2.0` floor and §8.5 the
  wrapped names. A new `a2a-sdk` major means re-verifying §8.5.
- #225 adds inbound governance to the executor this ADR introduces; it does not
  change the package's place in the stack.

# ADR 0025: `Donkey.simulate_a2a()` resolves declared agents offline and lazily imports the simulator

- **Status:** Accepted
- **Date:** 2026-11-07
- **Issue:** #1652

## Context

ADR 0023 set out the `simulate_*` family, with one method per surface, and
shipped `Donkey.simulate_mcp()`. It left `simulate_a2a` to follow its rules 1,
3 and 4, and asked for a new ADR if A2A needed a different discovery rule.

It does. An outbound `donkey.a2a.send()` or `stream()` call (ADR 0022) does
four things before its JSON-RPC send:

1. it runs `registry.explain()`;
2. it runs `registry.resolve_agent()`;
3. it fetches the agent card from the resolved endpoint;
4. it picks the SDK transport for the card's JSON-RPC interface.

Each of these needs a control plane or a live agent. The packaged A2A captures
(#1393, `simulator/_fixtures/anypoint/a2a_policies/`) were only reachable
through ingress replay for an agent the developer serves. They could not be
reached at the call site of an agent the developer calls.

## Decision

1. **Outbound only.** `Donkey.simulate_a2a(capture, *, agents, version="1.0",
   times=1, reply=None)` covers `donkey.a2a`. Inbound A2A keeps
   `donkey mock` / `donkey dev --scenario` ingress replay. Because
   `donkey.a2a` is provisional (ADR 0020), so is this method, and the
   snapshot lists it under `PROVISIONAL_MEMBERS`.
2. **Declared agents replace the registry, inside the block only.** Inside
   the block, the facade resolves only the refs listed in `agents`:
   - Each declared ref gets an endpoint on the reserved
     `simulated.donkey-kit.invalid` host.
   - A declared ref skips `explain()` and `resolve_agent()`, so it runs no
     registry read and no endpoint check.
   - Any other ref raises `RegistryError` before any request is sent.

   This differs from ADR 0023 in one way. MCP marks declared servers governed
   with a `simulated` check, because `ToolSet` carries a governance report.
   An A2A handle has no report, so the governance gate is skipped instead.
3. **Names are the captures.** `capture` is `<policy>-<send|stream>`, and
   `version` selects the protocol. The table `simulator.a2a.CAPTURES` lists
   exactly the packaged shapes. A pair with no capture raises `ValueError` on
   entry: v0.3 has no stream capture and no Azure block capture.

   A capture refuses only its own JSON-RPC method:
   - `-send` refuses `SendMessage` / `message/send`.
   - `-stream` refuses `SendStreamingMessage` / `message/stream`.

   The raised error is whatever the existing `classify()` gives the served
   response. This ADR does not change any mapping.
4. **Fail closed, offline.** One transport fronts the shared async client
   while the block is open, and it answers the declared host only:
   - It serves the agent card, with one JSON-RPC interface at `version`.
   - The next `times` matching calls get the capture verbatim, with
     `x-donkey-simulator: true`.
   - Every other call gets a simulated reply.

   Requests to any other host are delegated unchanged. The `a2a_view()`
   scope, the official SDK transports and classification all still run.
   After exit, the previous transport is restored and the refs resolve
   through Exchange again. Blocks do not nest (`RuntimeError`), and the scope
   belongs to one `Donkey`.
5. **The layering holds.** `a2a/` never imports the simulator. The facade
   gains a neutral slot, `A2AFacade.simulated(agents, auth=)`, which is set
   through the `_testing.a2a_discovery_scope` seam. `Donkey.simulate_a2a()`
   imports `simulator.a2a` lazily. The entry
   `donkey_kit.donkey -> donkey_kit.simulator.a2a` is added to the same two
   `ignore_imports` lists as `simulator.inject` and `simulator.mcp`.
6. **No new wire facts (§0.3).** The only gateway bytes are the #1393
   captures. The offline card and reply use the JSON shapes the official SDK
   parses, which the existing outbound tests already exercise.
   `docs/verified-apis.md` is unchanged.

### Alternatives considered

- **Make the facade's `explain()` answer from a simulated governance
  report.** Rejected: it would invent a report for a ref no registry holds.
  Skipping the gate for declared refs is the honest form, and an undeclared
  ref still fails.
- **One name per capture shape (`v1-pii-send`).** Rejected: it puts the
  protocol version in every name. `version=` matches how a caller already
  thinks about the agent.
- **Inject into the stream regardless of method.** Rejected: a `-send`
  capture served to a stream call would replay bytes the gateway never sent
  for that method.

## Consequences

- `ARCHITECTURE.md` now lists three ignored imports.
- `website/content/simulator.mdx` documents outbound A2A fault injection,
  with the name table.
- The public API snapshot gains the provisional `Donkey.simulate_a2a`.
  Acceptance tracks it (ddk-acceptance-sync).

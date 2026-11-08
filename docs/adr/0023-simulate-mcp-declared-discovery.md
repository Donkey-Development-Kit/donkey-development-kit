# ADR 0023: `Donkey.simulate_mcp()` answers discovery from declared servers and lazily imports the simulator

- **Status:** Accepted
- **Date:** 2026-11-07
- **Issue:** #1391

## Context

BG §1.4 / §1.5 require a refusal to be testable without a gateway. For model
calls, `Donkey.simulate(PIIDetected)` already does this. It lazily imports
`simulator.inject`, which is the one `ignore_imports` entry in the layers
contract and the "top-level package exports only production layers" contract
in `python/pyproject.toml`.

For MCP tool calls, #1391 first shipped the low-level
`donkey_kit.simulator.mcp.simulate_mcp(tools, capture, *, baseline, times)`.
It needs a `ToolSet` built by hand and an `httpx.MockTransport` baseline. A
developer's agent normally gets its `ToolSet` from `donkey.tools.discover()`,
and that reads Exchange and API Manager, so the agent's own code path cannot
run offline. An earlier decision kept `donkey.simulate()` model-only, and the
helper also left discovery out of scope.

Two questions needed a decision:

1. What the developer-facing MCP form is called.
2. How discovery behaves offline.

## Decision

1. **One `simulate_*` method per surface.** `Donkey.simulate(error)` stays
   model-only and unchanged. MCP tool calls get `Donkey.simulate_mcp(capture,
   *, servers, times=1, call_tool=None)`. The A2A counterpart is
   `simulate_a2a` (#1652). The surface is never inferred from a capture name.
2. **Declared servers stand in for Exchange, inside the block only.** While a
   `simulate_mcp()` block is open, that `Donkey`'s `donkey.tools.discover()`,
   `warm()` and the resolve behind `live_tools()` answer from the declared
   `servers` and never read the registry. Each declared server is marked
   governed by a `simulated` check and gets an endpoint on the reserved
   `simulated.donkey-kit.invalid` host. Discovery filters are not applied to
   declared servers. A ref that is not declared raises `RegistryError`.
3. **Fail closed.** Every set built inside the block shares one
   `McpReplay`. Unmatched MCP traffic is answered by an offline baseline
   built from the declared descriptors. After the block exits, those
   bindings refuse every request, and nothing falls back to a network.
   Blocks do not nest (`RuntimeError`), and the scope belongs to one `Donkey`.
4. **The layering holds.** `tools/` never imports the simulator. The
   override is a neutral slot on `Discovery`
   (`Discovery.simulated(servers, auth=, transport=)`), set through the
   `_testing.mcp_discovery_scope` seam. `Donkey.simulate_mcp()` imports
   `simulator.mcp` lazily inside the method, so `import donkey_kit` loads no
   simulator module. `donkey_kit.donkey -> donkey_kit.simulator.mcp` is added
   to the same two `ignore_imports` lists as `simulator.inject`. This is
   enforced by `lint-imports` and by
   `tests/unit/test_donkey_simulate_mcp.py::test_import_donkey_kit_loads_no_simulator_module`.
5. **No new wire facts (§0.3).** Only the packaged Omni Gateway captures are
   injected, verbatim, with `x-donkey-simulator: true`. The supported
   bindings and version floors are those of the low-level helper. ADK and
   CrewAI are refused (docs/verified-apis.md §9).

### Alternatives considered

- **`donkey.simulate("mcp", "pii-request")`.** Rejected: one method whose
  meaning changes with a string argument types poorly, and it overloads a
  stable signature.
- **Infer the surface from the capture name.** Rejected: names collide across
  surfaces (`pii-*` exists for model, MCP and A2A).
- **Keep discovery out of scope (helper only).** Rejected: the agent's real
  `discover()` call could not run offline, which defeats the goal of testing
  the agent's own code path.
- **Simulate Exchange search responses.** Rejected: it would invent registry
  wire shapes for a test feature, and §0.3 forbids that.

## Consequences

- `ARCHITECTURE.md` names two ignored imports instead of one.
- `website/content/simulator.mdx` and the generated API reference document
  `donkey.simulate_mcp()` as the normal path. The low-level helper stays
  documented for a hand-built `ToolSet`.
- The public API snapshot gains `Donkey.simulate_mcp`. The acceptance repo
  tracks the new member (ddk-acceptance-sync).
- `simulate_a2a` (#1652) should follow rules 1, 3 and 4. If it needs a
  different discovery rule, it gets its own ADR.

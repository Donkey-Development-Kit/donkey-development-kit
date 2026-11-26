# Decorators

Status: Live. Shipped, and verified against a real governed gateway.

<span id="decorators" />

## `@donkey.governed`

Runs a function inside a [`donkey.run()`](https://docs.donkey-kit.dev/correlation.md) scope:

```python
@donkey.governed(team="support")
async def handle_ticket(ticket): ...
```

One decorator gives the function a run/correlation ID, cost tags, an OTel span,
and typed refusals — the four things you would otherwise set up per call site.

- Wraps **both sync and async** callables.
- Works bare (`@donkey.governed`) or parametrised
  (`@donkey.governed(team=…, project=…, env=…, enduser_id=…)`).
- Takes **no `id=`**: each call opens its own run, so unrelated calls are never
  collapsed into one correlation. When you need a specific id, use
  `donkey.run(id=…)` directly.

## `@donkey.tool`

```python
@donkey.tool
async def lookup_crm(customer_id: str) -> dict:
    """Look up a customer record by id."""
    ...
```

Marks a function as a governed tool **without changing how it's called**. It
returns the same function with a `__donkey_tool__` marker and records a
`ToolSpec` (name, qualname, signature, docstring, `is_async`) in a
process-global registry you read with `registered_tools()`. Both `ToolSpec` and
`registered_tools` are exported from `donkey_kit`.

A tool with **no docstring is rejected at decoration time** (`ValueError`) — an
undescribed tool is useless to a model and to a registry.

`@donkey.tool(approval="required", risk="financial")` gates the tool behind a
human decision: a call raises `ApprovalRequired` instead of running, and
`donkey.approvals.resolve(...)` runs it once someone approves. The approval is
client-side and advisory. `risk=` labels the approval, approval span events and
scan/Exchange publication metadata; it does not require approval on its own,
select a gateway policy or request gateway step-up. See
[Human-in-the-loop](https://docs.donkey-kit.dev/hitl.md#declaring-an-approval).

`@donkey.tool(server="sku-manager")` attributes the tool to the MCP server it
belongs to, by that server's **service ID**: lowercase kebab-case, recorded as
`ToolSpec.server`. The ID is permanent once a service is registered, so it is
always set by you and never inferred from a module path or function name. A
tool without `server=` is reported as **unattributed**, not guessed. Anything
else raises `ValueError` at decoration time.

## `@donkey.agent`

```python
@donkey.agent(name="support-triage", skills=["triage", "draft-reply"])
async def handle(task):
    """Triage a support ticket and draft a reply for a human to send."""
    ...
```

Marks an agent entry point, again **without changing how it's called**. It
returns the same function with a `__donkey_agent__` marker and records an
`AgentSpec` (name, qualname, docstring, skills, `is_async`) that you read with
`registered_agents()`; both are exported from `donkey_kit`. `name` is the
agent's service ID, with the same kebab-case rule as `server=`. The docstring is
required, because it is the agent's description. A blank or repeated skill
raises `ValueError`.

[`donkey scan`](https://docs.donkey-kit.dev/cli/ship.md#donkey-scan) reads the `@donkey.tool` and `@donkey.agent`
markers, and [`donkey.serve()`](https://docs.donkey-kit.dev/registry/serve.md) generates its A2A agent card from the same
`@donkey.agent` record, so the scan and the card can't drift.

## Next steps

- [CLI](https://docs.donkey-kit.dev/cli.md): the `donkey` commands that take a decorated agent from code to a publish plan.
- [Local simulator](https://docs.donkey-kit.dev/simulator.md): rehearse the governed calls a decorated function makes, offline.
- [Typed refusals](https://docs.donkey-kit.dev/errors.md): every exception a governed call can raise.

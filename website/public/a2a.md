# A2A agents

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

Three steps take an agent from "runs on my laptop" to "callable by other agents,
through the gateway": **serve** it, **expose** it, and **develop** against a
gateway locally. `donkey.serve()` ships today. `donkey expose` and `donkey dev`
are on the [Roadmap](https://docs.donkey-kit.dev/roadmap.md).

## The setup it replaces

Today, accepting a task from a partner agent means:

1. writing a server
2. writing an agent card
3. adding logging and tracing
4. getting a certificate, opening a port and registering the agent by hand

`donkey.serve()` takes care of the first three today:
[governed inbound](#governed-inbound) gives every task a correlation ID and a
tracing span. `donkey expose`, which hands the fourth to the gateway, is on the
Roadmap.

## Why not have users call the a2a-sdk directly?

They could, but they'd get a bare server. The wrapper adds four things:

| Raw a2a-sdk | `donkey.serve()` |
|---|---|
| You write the agent card by hand | The card is generated from the same `@donkey.agent` / `@donkey.tool` markers `donkey scan` reads, so they can't drift |
| Incoming tasks arrive anonymous and untracked | They get the same correlation ID, cost tags and tracing span as outgoing calls |
| A blocked downstream call becomes a generic error | It becomes a typed refusal the caller understands |
| It binds wherever you configure it | It binds to localhost by default, behind the gateway |

> The differentiator is governed inbound and one-command exposure, not the protocol.

In short, the a2a-sdk handles the protocol and DDK adds the governance.
`donkey.serve()` runs the official `a2a-sdk` (its request handler, task store
and routes) and never reimplements the protocol, so any A2A client can call
your agent.

## `donkey.serve()` — the listener, in one line

### Install the extra

```bash
pip install "donkey-kit[a2a]"
```

The `[a2a]` extra brings the official `a2a-sdk` and `uvicorn`. `import donkey_kit`
never loads them, and calling `donkey.serve()` without the extra raises an
`ImportError` that names this command.

### Mark the agent and serve it

```python
from donkey_kit import A2AResult, A2ATask, Donkey

donkey = Donkey()

@donkey.agent(name="support-triage", skills=["triage", "draft-reply"])
async def handle(task: A2ATask) -> A2AResult:
    """Triages a support ticket and drafts a reply."""
    return A2AResult(text=f"triaged: {task.input}", data={"priority": "high"})

donkey.serve(handle)  # A2A server on 127.0.0.1:8000, card generated
```

### Call it from any A2A client

The card is served at `/.well-known/agent-card.json`, and tasks arrive as
JSON-RPC at `/`. Plain `curl` is enough:

```bash
curl -s http://127.0.0.1:8000/ \
  -H 'A2A-Version: 1.0' -H 'content-type: application/json' \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "SendMessage",
       "params": {"message": {"messageId": "m1", "role": "ROLE_USER",
                              "parts": [{"text": "printer on fire"}]}}}'
```

### What the card says

The card is built from your `@donkey.agent` marker and nothing else, so there is
no second description of your agent to keep in sync:

| Card field | Comes from |
|---|---|
| `name` | `@donkey.agent(name=...)`, the agent's service ID |
| `description` | the handler's docstring, which `@donkey.agent` requires |
| `skills` | `@donkey.agent(skills=...)` |
| `version` | `donkey.serve(version=...)`, `"0.0.0"` by default |
| `supportedInterfaces[0].url` | `donkey.serve(url=...)`, or `http://{host}:{port}/` |

[`donkey scan`](https://docs.donkey-kit.dev/cli.md#donkey-scan) reads the same marker, which is why the two
can't drift. A handler without `@donkey.agent` is rejected with a `TypeError`.

### What the handler gets and returns

The handler takes an `A2ATask`:

| Field | Holds |
|---|---|
| `input` | the text parts of the caller's message, joined by newlines |
| `data` | the structured (data) parts, in order |
| `task_id`, `context_id` | the A2A task and conversation IDs |
| `correlation_id` | the run ID every governed call in the task sends ([governed inbound](#governed-inbound)) |
| `message` | the native `a2a-sdk` message, for anything else (files, metadata) |

It returns a `str`, or an `A2AResult(text=..., data=...)` when it has structured
output. Each field that is set becomes one artifact on the completed task. A
sync handler works too: it runs in a worker thread.

If the handler raises, the task fails and the caller sees only the exception's
type, in the shape [governed inbound](#governed-inbound) describes. The
exception's message can hold internal detail, so it goes to your logs (the
`donkey_kit.a2a` logger), never to the caller. The server keeps serving.

### Where it listens

`donkey.serve()` binds to `127.0.0.1:8000`, so your agent is never the public
face; the gateway is. Pass `host=` and `port=` to change that. When a gateway
fronts the agent, pass its public URL as `url=` so the card advertises the
gateway, not your laptop:

```python
donkey.serve(handle, port=9000, url="https://gw.example.com/agents/support-triage/")
```

`donkey.serve()` blocks until the server stops. To mount the agent in your own
ASGI server, or to test it in process, build the same app with
`donkey_kit.a2a.build_app(handle)` and run it inside its ASGI lifespan, as
uvicorn does, so shutdown drains in-flight tasks.

  **What "Offline-verified" means here.** `donkey.serve()` has been run end to
  end on a real port: a plain `curl` fetched the generated card and got a
  completed task back. It has not yet been run behind a governed gateway, which
  is what `donkey expose` adds.

## Why the agent still runs a listener

An A2A agent *is* a server: it serves an agent card at a well-known path and
answers JSON-RPC task calls. Something must accept the socket, so the SDK hides
that listener behind one line rather than removing it.

What it can remove is everything painful around the listener — TLS, auth, rate
limits, public exposure, and registration. Those are the gateway's job. Omni
Gateway is an Envoy-based data plane whose policies compile to WASM; it runs as
a separate process in front of your agent, not as a library inside it.

  **Enforcement stays outside the agent's process.** If the enforcement point
  lived inside the agent, the agent's own code could bypass it. The gateway's
  value depends on being outside the thing it governs.

## Governed inbound

A task that arrives over A2A gets the same governance as a call your agent makes.
`donkey.serve()` wraps each task's handler in a `donkey.run()` scope, so the
downstream calls it makes are grouped and attributed with no code in the handler.

**One correlation ID per task.** If the caller sends `X-Correlation-Id`, the task
adopts it. If the header is missing or malformed, the task gets a fresh ID.
Malformed means anything other than 1 to 128 letters, digits, `.`, `_`, `:` or
`-`. Every governed call in the handler sends that ID, so the gateway joins the
caller's request and your agent's model calls on one key. The handler reads it as
`task.correlation_id`. If you renamed the header with the `correlation_header`
setting, the renamed header is the one read.

**Cost tags.** The client's configured cost tags ride every downstream call, as
the `donkey.cost.*` span attributes, and as headers when `send_cost_headers` is
on. A `donkey.run(team=...)` or `donkey.attribution(...)` block inside the
handler layers on top for its block. The caller's own cost headers are never
adopted: who pays is your agent's configuration, not the caller's claim.

**One span per task.** A `donkey.a2a.task` SERVER span wraps the handler, so the
`donkey.llm.chat` spans of its model calls are its children. If the caller sends
a W3C `traceparent`, the task span joins the caller's trace. The span carries:

| Attribute | Value |
|---|---|
| `donkey.correlation_id` | the task's run ID |
| `donkey.correlation_id.source` | `adopted` (the caller sent it) or `minted` |
| `donkey.a2a.agent` | the `@donkey.agent` name |
| `donkey.a2a.task_id`, `donkey.a2a.context_id` | the A2A IDs |
| `error.type` | the exception type, on a failed task only |

A failed task marks the span as an error with the exception's type only, never
its message. With `telemetry` off, no span is opened, but the run is still bound.

**Typed refusals, as a well-formed A2A failure.** When a downstream call is
refused, the framework's wrapped error is re-raised as its typed `DonkeyError`
first (the [typed-refusal bridge](https://docs.donkey-kit.dev/errors.md)). The task then fails with a status
message of two parts: a text part, and an `error` data part the caller can
branch on:

```json
{
  "state": "TASK_STATE_FAILED",
  "message": {
    "parts": [
      {"text": "the agent's downstream call was refused: PIIDetected"},
      {"data": {"error": {
        "type": "PIIDetected",
        "refusal": true,
        "correlation_id": "caller-run-42",
        "call_id": "9f1c0e…"
      }}}
    ]
  }
}
```

`refusal` is `true` for a governance refusal (a `PolicyViolation`), and `false`
for any other failure. Any other failure reads `the agent failed: `
and carries the same `error` part. `call_id` appears when the failure was a
governed call's. The refusal's `remediation` and the gateway's response body go
to your logs, not to the caller: they name your configuration and can repeat
the request's content.

`donkey_kit.a2a.build_app()` does the same for an app you mount yourself. Pass
`telemetry=`, `correlation_header=` and `refusals=donkey_kit.typed_refusals()` to
match `donkey.serve()`, which fills them from the client's configuration.

  **Not inbound enforcement.** Nothing here blocks an inbound task. Inbound
  policy (auth, rate limits, PII screening of the caller's message) is the
  gateway's job, in front of the agent.

## `donkey expose` — the ingress, registered from code Roadmap

```
$ donkey expose --env prod
  ✓ A2A proxy  https://gw.acme.internal/agents/support-triage
  ✓ policies   token-budget, pii-detection, trusted-agent-identity
  ✓ registry   support-triage v1.4.0
```

The gateway does the work — creating the A2A proxy, attaching the policy set,
and registering the card. `donkey expose` turns that console session into one
command that runs in CI. The same command will put an MCP server behind the
gateway: an MCP instance in front of it, its policies, and its Exchange entry.

It is planned to stay narrow, so it never turns into a provisioning tool:

- It creates only the instance in front of *your* agent or MCP server, on a
  gateway target that already exists.
- Running it again changes nothing that is already there. It adds a missing
  policy, and it never deletes, edits a policy's configuration, or moves an
  instance.
- `--dry-run` prints what it would create, and each step reports its own
  result.
- Gateways, targets, LLM proxies, organization-wide policy and connected-app
  scopes belong to your platform team and its infrastructure-as-code. If one is
  missing, `donkey expose` tells you which one rather than creating it.

The calls it needs are not verified against a live sandbox yet, so the command
does not ship until they are.

## `donkey dev` — a gateway in front of your laptop Roadmap

`donkey dev` puts a gateway in front of `donkey.serve()` on your machine, so you
can call your agent over A2A through governance before deploying. Two modes are
planned:

- **A real gateway.** Where a self-managed Omni Gateway image is available for
  local development, `donkey dev` runs it alongside `donkey.serve()`, so A2A
  calls pass through real policies.
- **Simulated ingress.** The [local simulator](https://docs.donkey-kit.dev/simulator.md) gains an A2A ingress
  mode: a local fake gateway in front of `donkey.serve()`, replaying the same
  rejection fixtures. A plain A2A client sees byte-identical responses, and
  every response carries `x-donkey-simulator: true`.

## Related

- [A2A agent tools](https://docs.donkey-kit.dev/tool-access/a2a.md) — call *other* A2A agents as tools.
- [Scan & publish](https://docs.donkey-kit.dev/publishing.md) — register your agent card with Agent Registry.
- [Local simulator](https://docs.donkey-kit.dev/simulator.md) — the fixture-replay server behind simulated ingress.
- [API reference](https://docs.donkey-kit.dev/reference/api.md#donkeyserve) — every `donkey.serve()` argument.

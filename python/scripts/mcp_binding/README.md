# MCP tool binding probes (docs/verified-apis.md §9)

Local round-trip evidence for the "MCP tool binding classes" rows of the
ledger (#636-#652). Each probe binds one framework's own MCP tool class to a
real MCP server running on 127.0.0.1, lists the tools, calls one, and writes
what it observed to `tests/fixtures/mcp_binding/<framework>.json`. Only
`bind_live.py` touches Anypoint or a gateway.

Files:

- `server.py` - the reference server (official `mcp` SDK, streamable HTTP at
  `http://127.0.0.1:<port>/mcp/`, trailing slash on purpose: the governed gateway
  only routes that form, ledger §6). Tools: `lookup_sku(sku: str) -> dict` and
  `add(a: int, b: int) -> int`. `--port N`, `--record PATH` (one JSON line per
  HTTP request: method, path, header names, and the values of `client_id`,
  `client_secret` and `x-correlation-id` only). Prints `READY <port>` once
  listening; stops cleanly on SIGTERM. Works on `mcp` 1.x (`FastMCP`) and 2.x
  (`MCPServer`). `--deny TOOL` answers a `tools/call` of that tool with the
  gateway's MCP access denial (HTTP 200, JSON-RPC `-32008` "Unable to access",
  ledger §9, #303) while still listing it.
- `_harness.py` - what the probes import (see the contract below).
- `probe_mcp_sdk.py` - the baseline: the plain `mcp` client against the server.
- `probe_<framework>.py` - one per framework.
- `probe_denial.py` - every installed framework against `server.py --deny add`:
  whether its tool object raises the denial (and as what) or returns it (#312).
  `--only NAME ...`, `--write --name VENV` writes
  `tests/fixtures/mcp_binding/denial-<VENV>.json`. Run it in each venv, since
  the frameworks' `mcp` pins conflict.
- `bind_toolset.py` - the SDK's `ToolSet.<framework>()` bindings against two
  local servers: filter, collision rename and approval gate (ledger §9).
- `bind_live.py` - the same bindings through a live gateway:
  `discover(governed=True)` from the org in `ANYPOINT_*`, then
  `--bind FRAMEWORK=ASSET` per pair, `--call TOOL JSON --want TEXT`. With
  `--tokens PATH` (a JSON object of asset ID to bearer token, for instances
  behind JWT Validation) it also checks that another instance's token raises
  `AuthError`; without it, the default `DONKEY_LLM_PROXY_CLIENT_ID` /
  `_CLIENT_SECRET`. Credentials come from the environment or that file, never
  argv.

## Running a probe

Never install into the repo's dev venv. Use a throwaway one, from `python/`:

```bash
uv venv --python 3.12 /tmp/ddk-mcpbind-<fw>/venv
VIRTUAL_ENV=/tmp/ddk-mcpbind-<fw>/venv uv pip install <framework> mcp
/tmp/ddk-mcpbind-<fw>/venv/bin/python scripts/mcp_binding/probe_<fw>.py --write
```

Install the framework's MCP binding package too where it is separate:
`langchain-mcp-adapters` for LangGraph, `llama-index-tools-mcp` for LlamaIndex,
`crewai-tools[mcp]` for CrewAI (which also needs `mcp<2`: `mcpadapt` uses the
1.x `streamablehttp_client`).
LangGraph also needs `mcp<2`: `langchain-mcp-adapters` 0.3.2 requires it, and
the 0.3.1 a resolver picks without the pin does not import on `mcp` 2.x. The
baseline fixture was recorded on `mcp<2`; the probe also passes on `mcp` 2.x,
where its `distributions` differ.

The probe launches `server.py` with its own interpreter (`sys.executable`), so
the framework's venv needs `mcp` (and `uvicorn`, which `mcp` depends on).
Without `--write` a probe prints the fixture and writes nothing.

## Probe contract

```python
from _harness import (
    local_mcp_server,
    headers_seen,
    read_requests,
    distributions,
    write_fixture,
)

with local_mcp_server() as srv:  # starts server.py on a free port, waits for READY
    srv.url  # "http://127.0.0.1:<port>/mcp/"
    srv.record  # Path of the JSONL request record
    ...  # bind the framework's MCP tool class to srv.url,
    # sending client_id / client_secret (fake values)
    seen = headers_seen(srv.record)
# leaving the block SIGTERMs the server

write_fixture("<framework>", fixture)  # tests/fixtures/mcp_binding/<framework>.json
```

- `local_mcp_server(*, deny=None)` - context manager yielding
  `LocalServer(url, record, process)`; `deny` starts the server with `--deny`.
- `read_requests(record) -> list[dict]` - every recorded request.
- `headers_seen(record) -> {"client_id": ..., "client_secret": ...}` - values
  the server actually received (`None` if never sent).
- `distributions(*names) -> {name: version}` - installed versions; `mcp` is
  always included. Pass the framework's distribution name(s).
- `write_fixture(name, data) -> Path` - sorted keys, indent 2.

## Fixture format

```json
{
  "framework": "adk",
  "distributions": {"google-adk": "x.y.z", "mcp": "x.y.z"},
  "binding": {"class": "McpToolset", "module": "google.adk.tools.mcp_tool", "call": "how it was constructed and invoked"},
  "url_shape": "http://127.0.0.1:<port>/mcp/",
  "tools_listed": ["add", "lookup_sku"],
  "native_tool_types": ["the framework's own tool class names"],
  "tool_call": {"name": "add", "args": {"a": 2, "b": 3}, "result": "what came back"},
  "headers_seen": {"client_id": "fake-client-id", "client_secret": "fake-client-secret"},
  "date": "2026-10-28"
}
```

Only record what the run produced. If a binding cannot send custom headers, or
cannot reach the trailing-slash URL, record that outcome (and the error) instead
of a success; do not paper over it.

The fixtures sit under `tests/fixtures/`, so `tests/fixtures/fixtures.lock`
hashes them. After writing one, relock from a source checkout:
`python -m donkey_kit.simulator.fixtures --relock`.

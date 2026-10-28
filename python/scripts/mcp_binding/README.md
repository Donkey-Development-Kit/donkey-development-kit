# MCP tool binding probes (docs/verified-apis.md §9)

Local round-trip evidence for the "MCP tool binding classes" rows of the
ledger (#636-#652). Each probe binds one framework's own MCP tool class to a
real MCP server running on 127.0.0.1, lists the tools, calls one, and writes
what it observed to `tests/fixtures/mcp_binding/<framework>.json`. Nothing here
touches Anypoint or a gateway.

Files:

- `server.py` - the reference server (official `mcp` SDK, streamable HTTP at
  `http://127.0.0.1:<port>/mcp/`, trailing slash on purpose: the governed gateway
  only routes that form, ledger §6). Tools: `lookup_sku(sku: str) -> dict` and
  `add(a: int, b: int) -> int`. `--port N`, `--record PATH` (one JSON line per
  HTTP request: method, path, header names, and the values of `client_id`,
  `client_secret` and `x-correlation-id` only). Prints `READY <port>` once
  listening; stops cleanly on SIGTERM. Works on `mcp` 1.x (`FastMCP`) and 2.x
  (`MCPServer`).
- `_harness.py` - what the probes import (see the contract below).
- `probe_mcp_sdk.py` - the baseline: the plain `mcp` client against the server.
- `probe_<framework>.py` - one per framework.

## Running a probe

Never install into the repo's dev venv. Use a throwaway one, from `python/`:

```bash
uv venv --python 3.12 /tmp/ddk-mcpbind-<fw>/venv
VIRTUAL_ENV=/tmp/ddk-mcpbind-<fw>/venv uv pip install <framework> mcp
/tmp/ddk-mcpbind-<fw>/venv/bin/python scripts/mcp_binding/probe_<fw>.py --write
```

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

- `local_mcp_server()` - context manager yielding `LocalServer(url, record, process)`.
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

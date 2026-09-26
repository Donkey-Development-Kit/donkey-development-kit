# FastMCP native and protocol descriptors offline verification

Date: 2026-09-26. Base: `ee56f8112f633b0368629376b59fcf5fc1f1f2d1`.
Python 3.11.16, macOS arm64. Issue #653. Scope: §10.

## Findings and limits

`fastmcp==3.4.7`, `fastmcp-slim==3.4.7`, `mcp==1.30.0`.

- §10: native `mcp.server.fastmcp.tools.base.Tool` (owned by `mcp`) and `fastmcp.tools.FunctionTool` (implementation `fastmcp.tools.function_tool.FunctionTool`, owned by `fastmcp-slim`) expose string `.name`/`.description` and dict `.parameters`. Neither has the guessed native `.inputSchema` attribute at these versions.
- The protocol representation is `mcp.types.Tool`, with `.inputSchema` equal to the native `.parameters` schema. The probe verifies a required string property from a real trivial function and preserves each implementation's already-computed schema, including their differences.
- SDK FastMCP `list_tools()` returns protocol tools. Standalone FastMCP `list_tools()` returns native tools; their `.to_mcp_tool()` performs the protocol conversion. All methods were called in-process; no server, MCP session, remote `tools/list` RPC, or tool invocation was started.
- Package ownership is checked against installed distribution file records: `fastmcp==3.4.7` is the entry-point distribution, while `fastmcp-slim==3.4.7` owns the implementation. An initial owner check against `fastmcp` failed; the corrected probe asserts the actual owner rather than omitting that check.

This corrects the ledger's object-kind assumption, not a proven upstream regression. There is no FastMCP §9 row. DDK has no implemented per-framework descriptor readers to change; its introspection guidance now points to version-specific evidence, and `derive_descriptor()` remains guarded.

The probe's audit hook denies socket connections and DNS resolution before framework imports. No Anypoint credentials or gateway are needed. Local construction and schema inspection do not promote conformance support or establish live MCP behavior. No `_verify` value is changed.

## Evidence and reproduction

- [apis.txt](apis.txt): raw stdout/stderr from the executable probe with `--emit-verified`, exit 0.
- `section-*.txt`: separately executed section selectors, exit 0.
- [requirements.txt](requirements.txt): exact installed packages in the probe environment, excluding editable DDK. [pip-check.txt](pip-check.txt) records dependency consistency.
- [commands.json](commands.json): actual commands, exit codes, source path and probe SHA-256.

The disposable probe environment was installed with `.[dev,agent_framework,mcp]` plus `fastmcp`; it is separate from the seven-framework environment in PRs #643–#649. These pins record the tested environment, not new dependency ceilings or a claim of compatibility across arbitrary resolves.

From `python/`, in a new Python 3.11 environment:

```bash
python -m pip install -r ../docs/evidence/frameworks/fastmcp/requirements.txt
python -m pip install --no-deps -e .
python scripts/verification/verify_fastmcp_apis.py --emit-verified
python scripts/verification/verify_fastmcp_apis.py --section 10 --emit-verified
```

The probe removes inherited `DONKEY_LLM_PROXY_*`, `ANYPOINT_*`, `MULESOFT_*` and proxy environment variables in its child process. It leaves telemetry variables untouched. Missing packages, changed symbols, incorrect schema shapes, or attempted network access fail the probe rather than becoming skips.

## Validation

Each issue worktree is tested using its own source path. Separate environments keep optional framework dependencies out of the base-only gate:

- [Framework-free unit gate](gate-base-only.txt): `.[dev]`, `pytest -q tests/unit`: 640 passed, 59 skipped.
- [Standard CI gate](gate-pytest.txt): `.[dev,llm,cli]`, `pytest -q`: 682 passed, 47 skipped, 6 deselected.
- [All-framework gate](gate-all-frameworks.txt): `.[all,dev]` plus MCP extension packages, `pytest -q`: 771 passed, 1 skipped, 13 deselected; this separate compatible resolve is pinned in [gate-all-frameworks-requirements.txt](gate-all-frameworks-requirements.txt).
- [mypy](gate-mypy.txt), [Ruff](gate-ruff.txt), [import boundaries](gate-imports.txt), [whitespace](gate-diff.txt).

Inherited credentials and proxy settings are cleared for these gates. OpenTelemetry settings are untouched so telemetry assertions execute; CrewAI's own telemetry is disabled and its storage points to a disposable directory. No test, exemption or skip condition was changed. Skips reflect existing optional-dependency gates, and infrastructure/benchmark suites retain their normal opt-in selection.

Ledger findings are synchronized with `website/content/reference/unsupported-boundary.mdx`; `concepts/verification.mdx` does not exist on this base. The guards remain intact while the ledger records the narrower offline result.

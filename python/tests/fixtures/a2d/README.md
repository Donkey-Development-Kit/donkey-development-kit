# Captured fixtures — A2D platform MCP tools (shape verification)

These JSON files are **faithful, trimmed captures** of real responses from the
`mcp-a2d` MCP server (host `www.a2d-ai.com`), taken 2026-08-28. They exist so the
registry value types (`McpServerHandle`, `ToolDescriptor`, `AssetRef`) can be
validated against real-world shapes without a live call (BG §1.5, fixture-driven).

## What these are — and are NOT

The A2D platform is an agent/API **design + mocking + Exchange-publishing**
tool. It is Anypoint-adjacent (its `publish_to_exchange_*` tools take separate
Anypoint credentials; server specs carry `"platform": "mulesoft"`), but it is
**not** the Anypoint Exchange control-plane REST API the SDK is designed to call
directly. These captures came through MCP tool calls, which wrap an unknown
backend contract. Therefore:

- The **runtime MCP endpoint shape**, **tool-descriptor shape**, and
  **asset-identity shape** below are treated as VERIFIED and are used to
  validate the SDK's value types.
- The **direct Anypoint Exchange REST endpoints** (search, resolve, governed
  join) remain UNVERIFIED — see `docs/verified-apis.md`. `ExchangeRegistry`
  stays blocked; nothing here wires it to `www.a2d-ai.com`.

## Files

- `mcp_server_spec.account_management.json` — `get_mcp_server_spec` output,
  trimmed to 3 of 33 tools. Full shape preserved.
- `environments.sample.json` — `list_environments` output, one record per
  (asset_type × environment_type) observed.
- `mcp_servers.list.json` — `list_mcp_servers` output, first few records.

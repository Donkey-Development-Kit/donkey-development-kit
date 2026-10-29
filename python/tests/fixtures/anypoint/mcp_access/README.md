# Captured fixtures — MCP tool-access denials (#303)

Captured 2026-10-29 through Omni Gateway `1.14.4` (`shared-omni-gateway`, MCP
Support `1.1.1`) with MCP `2025-06-18` streamable HTTP, against two server
origins: a framework-built FastMCP server (behind JWT Validation) and a
Mule-hosted one (Mule 4.13, MCP Connector 1.7.0, behind Client ID
Enforcement). Both origins answered byte-identically, so one file per policy
covers both. Each policy was attached for the capture and removed after it.
The raw captures are on #303. Headers keep the non-volatile subset;
`x-correlation-id` and the session id are dropped.

## Files

- `tools-call.global-access-denied.{headers.txt,body.json}`: MCP Global Access
  (`mcp-global-access-policy` 1.0.1, a `Block` / `literal` rule on
  `current_time`) answering `tools/call current_time`. **HTTP 200** with a
  JSON-RPC `error`: `code` `-32008`, `message` `"Unable to access"`, `data`
  `"Access denied to: current_time"`. No `WWW-Authenticate`.
- `tools-call.abac-denied.{headers.txt,body.json}`: MCP Attribute-Based Access
  Control (`mcp-access-control` 1.0.2) answering `tools/call lookup_proxy` for a
  caller its Cedar rules don't permit. Same `code` and `message`; `data` quotes
  the tool and ends with a period: `"Access denied to: \"lookup_proxy\"."`.

Both policies also drop the denied tool from `tools/list` (HTTP 200, a normal
`result.tools` without it). No file is kept for that: it is an ordinary list.

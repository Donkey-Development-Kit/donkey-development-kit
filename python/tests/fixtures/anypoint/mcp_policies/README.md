# Live MCP policy captures (#1384, partial)

Captured 2026-11-02 in DDK organization
`826ba985-9894-4f7b-ba71-6965832ef0d6`, Sandbox environment
`14d3b31e-4e3b-4d90-b77a-63c9d6b7ea6a`, on API instance `21261437`
(`ddk-langgraph-mcp`, `shared-omni-gateway`, Omni Gateway `1.14.4`).
The upstream is the deployed framework-built server, not a local simulator.
All policy assets are from MuleSoft group `68ef9520-24e9-4cf2-b2f5-620025690913`.

Each file contains the observed HTTP status, selected response headers, and
the **unmodified response body as a string**. SSE framing remains intact.
Only `content-type` and `server` headers are retained; session/correlation IDs,
dates, transport and Heroku reporting headers are omitted. No request credential,
token, API key, or real PII is retained. `capture@example.com` is synthetic.

## Procedure

POST raw MCP JSON-RPC to the gateway's `/ddk-langgraph-mcp/` endpoint (the
trailing slash is required; without it this gateway returns an empty 404).
Authenticate using the existing `ddk-langgraph-agent` Keycloak client.
`initialize` uses protocol `2025-06-18`, empty capabilities and clientInfo
`{"name":"ddk-mcp-policy-capture","version":"1"}`. Adopt `mcp-session-id`,
send `notifications/initialized`, then each request with `id: 2` unless noted.

Attach exactly one policy after the standard chain, redeploy **this instance
only**, wait 30 seconds, and create a fresh MCP session. Merely adding the
policy to API Manager did not activate it in the data plane: the initial
pre-redeploy responses are not included as policy evidence. Remove the temporary
policy with `anypoint-cli-v4 api-mgr policy remove`, redeploy this instance again,
and wait 30 seconds before the next capture. No target-wide redeploy was used.
The private provisioning scratch harness is `tmp/capture_mcp_1384.py`.

## Requests And Configurations

| File | Policy/version and configuration | Request and observation |
| --- | --- | --- |
| `pii-request.json` | `mcp-pii-detector` `1.0.1`: `{"entities":["Email"],"action":"Reject"}` | `tools/call current_time`, arguments `{"timezone":"capture@example.com"}`. HTTP 403 JSON, `-32602`, request phase, masked email. |
| `pii-response-sse.json` | `mcp-pii-detector` `1.0.1`: `{"entities":[],"action":"Reject","customPatterns":[{"name":"synthetic-result","pattern":"42"}]}` | `tools/call calculate`, arguments `{"expression":"6*7"}`. Baseline is SSE text `42`; rejection is **HTTP 403 JSON**, not an error event in HTTP 200. `-32603`, response phase, masked custom finding. |
| `pii-mask-sse.json` | Same custom pattern, `action: "Log and mask"` | Same arithmetic request. HTTP 200 SSE, result text `**`. |
| `schema-invalid-args.json` | `mcp-schema-validation` `1.3.0`: `{"validateToolSchema":true}` | `tools/call calculate`, arguments `{"expression":42}`. HTTP 200 JSON, `-32602`; `data` is a string **echoing the rejected value**, not a policy discriminator. |
| `schema-missing-id.json` | Same schema-validation configuration | `tools/call calculate`, arguments `{"expression":"6*7"}`, **id omitted**. HTTP 200 JSON, `-32600`; response id is `1`. |
| `poisoning-block-list.json` | `mcp-schema-validation` `1.3.0`: `{"validateToolSchema":false,"poisoningDetection":{"drift":{"enabled":true,"action":"BlockResponse"},"shadow":{"enabled":true,"action":"BlockResponse"}}}` | `tools/list`, params `{}`. HTTP 200 SSE, `-32603`, four `inputSchema` drift findings. |
| `poisoning-remove-list.json` | Same detection configuration, both actions `RemoveTool` | `tools/list`, params `{}`. HTTP 200 SSE, empty catalog. |
| `progressive-list.json` | `mcp-tools-progressive-disclosure` `1.0.1`: `{}` (defaults) | `tools/list`, params `{}`. Only `search_tools` and `invoke_tool`. |
| `progressive-invalid-params.json` | Same progressive-disclosure configuration | `tools/call invoke_tool`, arguments `{}`. HTTP 200 JSON, `-32602`, `data.reason: "invalid_params"`; no `data.policy`. |
| `mapping-list.json` | `mcp-tool-mapping` `1.0.2`: `{"toolMappings":[{"sourceToolName":"calculate","mappedToolName":"arithmetic","mappedDescription":"Synthetic capture arithmetic tool.","mappingType":"literal"}]}` | `tools/list`, params `{}`. Gateway-visible tool is `arithmetic`, with the mapped description. |
| `mapping-call.json` | Same tool-mapping configuration | `tools/call arithmetic`, arguments `{"expression":"6*7"}`. HTTP 200 SSE, text `42`. |
| `optimization-html.json` | `mcp-tool-response-sanitization` `1.2.1`: `{"min_response_bytes":0}` (other defaults) | `tools/call calculate`, arguments `{"expression":"<p>DDK   capture</p>"}`. The upstream tool error echoes HTML; the policy converts it to Markdown and collapses whitespace. It remains a normal `isError: true` tool result, not a JSON-RPC refusal. |
| `abac-denied.json` | `mcp-access-control` `1.0.2`: `authType: "Other"`, rules `['permit(principal,action,resource) unless { action == Action::"tools/call" && resource == Tool::"calculate" };']` | `tools/call calculate`, arguments `{"expression":"6*7"}`. HTTP 200 JSON, `-32008`, quoted tool name and trailing period. `tools/list` also omitted `calculate`. |

The poisoning findings are **descriptor drift**, not evidence of a malicious
server. The published asset's input schemas omit runtime metadata/constraints;
enabling drift detection flags all four tools. `RemoveTool` therefore empties
the catalog. Do not describe this capture as prompt-injection detection.

## Restoration And Remaining Coverage

After every scenario the original policy IDs were present again:
`9667638` (MCP Support), `9667744` (JWT Validation), `9667745` (OAuth2 OBO),
`9667746` (API Key). After the final redeploy, discovery returned the original
four tools and `calculate("6*7")` returned HTTP 200 SSE text `42`.

**Not yet captured:** PII on a JSON-only upstream response, the elicitation
`decline` rewrite, base64/null/TOON payload transforms, and LLM trimming.
The JSON-response attempt on Mule-hosted instance `21263396` hit HTTP 401
`Authentication Attempt Failed` with the available consumer credentials; those
responses are not fixtures or verification evidence. Its temporary PII policies
were removed and its original policy IDs restored and redeployed. Issue #1384
stays open for the missing cases; #1390 owns detailed payload-transform coverage.
No SDK matcher, guard, or framework-support claim changes in this capture slice.

# Live MCP policy captures (#1384, partial)

Captured 2026-11-02 in the DDK organization, Sandbox environment,
on API instance `21261437`
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

### JSON-Only Response Follow-Up (2026-11-05)

`pii-response-json.json` is a separate live capture from an Anypoint sandbox,
with a deployed JSON-only Mule application `1.0.2` on Mule runtime `4.13.0`,
Omni Gateway `1.14.4`, and MCP Support definition `1.1.3` / implementation
`1.1.1`. Its sanitized provenance is `pii-response-json.provenance.json`.
No tenant UUIDs, deployment hosts, internal IDs, credentials or signed URLs
are included in this follow-up's provenance.

The request was `tools/call capture_contact` with arguments
`{"sample":"synthetic"}` and numeric id `3`: no PII was sent in the input.
Before attachment, both direct upstream and gateway returned HTTP 200
`application/json`, text `Synthetic contact: capture@example.com`, with
`isError: false`. The email is fixed synthetic response data, not user input.
MCP PII Detector definition `1.0.1` / implementation
`mcp-pii-detector-policy-flex` `1.0.2`, configured as
`{"entities":["Email"],"action":"Reject"}`, returned HTTP 403
`application/json`, JSON-RPC `-32603`, `data.phase="response"`, and
`masked_value="*******@example.com"`. This is not an SSE error event.

The fixture retains exactly the wire capture's status, selected `content-type`
and `server` headers, and UTF-8 response body string, including key order and
numeric response id `3`. No response-id scrub or body replacement was needed;
outer JSON formatting is not wire evidence. The private fixture-ready response
agrees with the original wire response on all three fields.

The collection report ended with an assertion failure after recording the
403 and restoring the chain. A subsequent read-only verification validated
that original capture without a second policy attachment: the exact original
chain was restored, no temporary policies remained, the instance was active,
deployment was `APPLIED/RUNNING`, and direct/gateway JSON calls succeeded.
Private reports and deployment receipts are not copied into this test tree.
This fixture is test-only, not packaged or simulator-served; it adds no SDK
classification, framework round-trip or elicitation evidence. The packaged
README remains the historical 2026-11-02 snapshot of its own capture set.

### Original SSE Capture Restoration

After every scenario the original policy IDs were present again:
`9667638` (MCP Support), `9667744` (JWT Validation), `9667745` (OAuth2 OBO),
`9667746` (API Key). After the final redeploy, discovery returned the original
four tools and `calculate("6*7")` returned HTTP 200 SSE text `42`.

**Not yet captured:** the elicitation `decline` rewrite and LLM trimming.
The JSON-response attempt on Mule-hosted instance `21263396` hit HTTP 401
`Authentication Attempt Failed` with the available consumer credentials; those
responses are not fixtures or verification evidence. Its temporary PII policies
were removed and its original policy IDs restored and redeployed. Issue #1384
stays open for elicitation; #1390 owns detailed payload-transform coverage.
No SDK matcher, guard, or framework-support claim changes in this capture slice.

### Payload Optimization Transforms (2026-11-07, #1639, #1640)

Test-only captures (not packaged or simulator-served, like `pii-response-json`)
from three DDK / Sandbox instances on `shared-omni-gateway`, each fronting the
`ddk-langgraph-mcp` Exchange asset `0.2.0` with MCP Support, one policy, then
Credential Injection (`X-DDK-Access-Key`). They are permanent policy-test
instances created by the provisioning repo's `scripts/deploy_policy_instances.py`,
not temporary attachments, so there was no chain to restore.

| Instance | Id | Policy `mcp-tool-response-sanitization` `1.2.1` configuration |
| --- | --- | --- |
| `ddk-langgraph-mcp-global` (control) | `21281858` | none (Global Access blocking `current_time`; it leaves other tools untouched) |
| `ddk-langgraph-mcp-sanitize-nulls` | `21281859` | `{"min_response_bytes":0,"remove_nulls":true}` (base64 stripping is the default) |
| `ddk-langgraph-mcp-sanitize-toon` | `21281860` | `{"min_response_bytes":0,"enable_toon_encoding":true}` |

Requests were `tools/call` with an unauthenticated MCP session (the gateway
injects the API key), `initialize` protocol `2025-06-18`. The upstream JSON
comes from the synthetic `capture_records` tool of the provisioning repo
(`nulls`: one record with null fields; `uniform`: twenty same-shape records);
the base64 cases echo a string through a `calculate` error. All values are
fixed synthetic data. The gateway runtime version was not recorded.

| File | Observation |
| --- | --- |
| `optimization-base64-control.json` | Data URI echoed untouched. |
| `optimization-base64.json` | The data URI became `[base64 data removed]`; `isError: true` stays a tool result. |
| `optimization-base64-bare.json` | A bare base64 string was **not** stripped. |
| `optimization-nulls-control.json` | JSON with `null` values, Python-style spacing. |
| `optimization-nulls.json` | Null keys removed at every depth, JSON re-serialized compact. |
| `optimization-toon-control.json` | The uniform array as JSON text. |
| `optimization-toon.json` | `TOON: [20]{id,name,status,score}:` text, not JSON. |
| `optimization-toon-ineligible.json` | A JSON object with nulls was not TOON-encoded. |

Trigger conditions beyond these cases (other shapes, size thresholds, SSE vs
JSON upstreams) were not explored. No SDK classification or decoding changes.

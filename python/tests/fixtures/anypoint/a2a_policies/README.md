# A2A instance policy captures

Captured 2026-11-02 on `ddk-langgraph-agent` (instance `21206128`), Sandbox,
through the managed `shared-omni-gateway`, Omni Gateway **1.14.4**. These are
actual JSON-RPC HTTP responses, not examples copied from policy documentation.
The captures are test-only; `donkey dev` does not replay them yet (#1397).
Response bodies retain their bytes; headers were scrubbed with
`scripts/scrub_fixtures.py`. No credentials or real PII were sent or retained.

Each policy was applied in isolation alongside the existing outbound
Credential Injection - API Key policy, then removed. The original policy IDs
were checked after each removal and the instance redeployed. A baseline and
restored `GetTask` for a nonexistent synthetic task both returned HTTP 200,
`-32001`, `TASK_NOT_FOUND`. Deployment and cleanup receipts are in #1393.

## Reproduce

All requests POST to the resolved gateway URL with its trailing slash,
`Content-Type: application/json` and `Accept-Encoding: identity`. These v0.3
requests do not send `A2A-Version`. The JSON-RPC request ID is `1393`.

- `v03-pii-send`: `a-two-a-pii-detector` **1.0.2**, `entities: [Email]`,
  `action: Reject`. `message/send`, message ID `ddk-1393-synthetic`, role
  `user`, one `{kind: text, text: "Email capture@example.com"}` part. HTTP 403,
  `-32602`, `Invalid params`. `error.data` is a **string** containing a JSON
  findings list, including `pii_type: Email`, the synthetic value, start 6 and
  end 25. Keep this synthetic value to test redaction (#1398), not to endorse
  forwarding gateway PII into exception text or telemetry.
- `v03-schema-params`: `a-two-a-schema-validation` **1.0.2**, empty config.
  `message/send`, `params: {message: {}}`. HTTP 400, `-32602`, missing `parts`.
- `v03-schema-method`: same policy, `unknown/method`, empty params. HTTP 400,
  `-32601`; the string `error.data` lists the allowed methods.
- `v03-schema-parse`: same policy, truncated body
  `{"jsonrpc":"2.0","id":1393,"method":"message/send","params":`.
  HTTP 400, `-32700`, `Invalid JSON data`. The response ID is **1**, not 1393.
- `v03-rate-send`: `a-two-a-token-rate-limit` **1.0.1**, `maximumTokens: 1`,
  `timePeriodInMilliseconds: 60000`, `keySelector: #["ddk-1393-capture"]`.
  `message/send`, the same message ID and role as the PII request, one text
  part containing `Synthetic capture request exceeds one token.`. HTTP 429,
  empty body (no body file), `x-token-limit: 1`, `x-token-remaining: 1`,
  `x-token-reset: 60000` milliseconds, **no `retry-after`**. A request larger
  than the window can be refused while remaining is positive; do not infer
  exhaustion from `x-token-remaining == 0`.

## Remaining Verification

These captures do not verify v1, guardrails, quality evaluation, accepted-call
rate headers, streaming refusals or the authorization-code challenge. #1393
remains open. Attaching `a2a-v1-pii-detector` 1.0.0 to this existing `a2a`
instance returned HTTP 400 `PolicyValidationError`: the policy requires an
`A2A_V1` asset type. No v1 policy was attached. A compatible registered asset
and instance must be verified before the v1 captures; an upstream speaking v1
does not by itself make the API Manager registration compatible.

Official policy references:

- https://docs.mulesoft.com/gateway/latest/policies-included-a2a-pii-detector
- https://docs.mulesoft.com/gateway/latest/policies-included-a2a-schema-validation
- https://docs.mulesoft.com/gateway/latest/policies-included-a2a-token-rate-limit

The PII response's string `data` is more structured than the epic's shorthand
`PII type: value`: it embeds the findings JSON. These v0.3 responses do not
carry v1 `google.rpc.ErrorInfo` details. Schema method-not-found is HTTP 400
here, not the v1 policy's documented HTTP 404.

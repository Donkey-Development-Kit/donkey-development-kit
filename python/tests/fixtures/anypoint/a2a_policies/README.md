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

## V1 Captures

Captured 2026-11-02 on temporary instance `21269788`,
`ddk-capture1393v1-agent` `1.0.0`, on the same Sandbox gateway (1.14.4).
The separate Exchange asset used `agent-metadata.protocol: a2a_v1`,
`properties.protocol: a2a_v1`, and classifier `a2a-v1-card`; the instance
used `endpoint.type: a2a_v1`. The original v0.3 instance was not modified.
The temporary instance was deleted and its asset version **hard-deleted**
after capture; control-plane reads confirmed both gone. Receipts are in #1393.

These requests send `A2A-Version: 1.0`, numeric request ID `1393`, and the
same content/encoding headers as above. Messages have role `ROLE_USER`,
message ID `ddk-1393-synthetic`, and parts `{text: ...}` without `kind`.
Policies ran in isolation, with no upstream access credential. These are
pre-upstream refusals, not successful agent round-trips or SSE streams.
The registration card is a staged v1-only projection of the hosted dual card;
it does not prove dual-card publication or the SDK publication path.

- `v1-pii-{send,stream}`: `a2a-v1-pii-detector` **1.0.0**, the same PII
  config and synthetic email as v0.3. `SendMessage` and
  `SendStreamingMessage`: HTTP 403, `-32600`, response ID **string** `"1393"`.
  `error.data` is an `ErrorInfo` list (`@type: type.googleapis.com/google.rpc.ErrorInfo`,
  `domain: a2a-protocol.org`, `reason: FORBIDDEN`). **Both** `error.message`
  and `metadata.detail` contain the raw synthetic findings. No `www-authenticate`.
- `v1-schema-params`: `a-two-a-v1-schema-validation` **1.0.1**, empty config,
  `SendMessage`, `params: {message: {}}`: 400 / `-32602`, `INVALID_PARAMS`,
  missing `messageId`, response ID string `"1393"`.
- `v1-schema-method`: same policy, `UnknownMethod`, empty params:
  **404** / `-32601`, `METHOD_NOT_FOUND`, response ID string `"1393"`.
- `v1-schema-parse`: same policy, truncated request
  `{"jsonrpc":"2.0","id":1393,"method":"SendMessage","params":`:
  400 / `-32700`, `PARSE_ERROR`, response ID **null**.
  All three schema responses carry an `ErrorInfo` list with the same type/domain
  and their diagnostic in `metadata.detail`.
- `v1-rate-{send,stream}`: `a-two-a-v1-token-rate-limit` **1.0.1**,
  one-token window, `timePeriodInMilliseconds: 60000`,
  `keySelector: #["ddk-1393-v1-capture"]`, the same oversized text as v0.3.
  Both methods return bare 429 with empty body and the same `x-token-*`
  values as v0.3, no `retry-after`. No body files exist for these empty responses.

## Remaining Verification

These captures do not verify guardrails, quality evaluation, accepted-call
rate headers, successful streaming or the authorization-code challenge. #1393
remains open. Attaching `a2a-v1-pii-detector` 1.0.0 to the existing `a2a`
instance returned HTTP 400 `PolicyValidationError`: the policy requires an
`A2A_V1` asset type. No v1 policy was attached. A compatible registered asset
and instance were verified separately above; an upstream speaking v1
does not by itself make the API Manager registration compatible.

Official policy references:

- https://docs.mulesoft.com/gateway/latest/policies-included-a2a-pii-detector
- https://docs.mulesoft.com/gateway/latest/policies-included-a2a-schema-validation
- https://docs.mulesoft.com/gateway/latest/policies-included-a2a-token-rate-limit

The PII response's string `data` is more structured than the epic's shorthand
`PII type: value`: it embeds the findings JSON. These v0.3 responses do not
carry v1 `google.rpc.ErrorInfo` details. Schema method-not-found is HTTP 400
here, not the v1 policy's documented HTTP 404.

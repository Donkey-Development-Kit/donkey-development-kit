# Model-wallet JWT ingress — LIVE capture (docs/verified-apis.md §2/§3, #372)

Captured **2026-09-21** from a real deployed, **wallet-enabled** Agent Network
LLM proxy in the DDK sandbox org `826ba985-9894-4f7b-ba71-6965832ef0d6`, env
**Sandbox** (`14d3b31e-4e3b-4d90-b77a-63c9d6b7ea6a`). Proxy asset
`ddk-model-wallet` v1.0, API Manager instance **`21186246`**, deployed to the
`shared-omni-gateway` Flex Gateway (v1.13.5), upstream `https://api.openai.com/v1/`,
routing to `openai:gpt-5-mini`.

This is the **parallel ingress model** to the `client_id`/`client_secret` pair
captured in `../llm_proxy/` — a wallet-backed proxy identifies the caller from an
**IdP-issued JWT + a client ID**, with **no `client_secret`**. It is the first
live confirmation of the model-wallet rows in `docs/verified-apis.md` §2/§3
(previously UNVERIFIED, doc-read only). See issue #372.

## How the caller authenticates (verified live)

- **JWT rides as `Authorization: Bearer <JWT>`.** This was an *assumption* in the
  source doc read (`Authorization: Bearer` is nowhere quoted on
  `docs.mulesoft.com/general/exp-model-wallets-manage`); it is now **verified** —
  the JWT Validation policy's `jwtOrigin` is `httpBearerAuthenticationHeader`, and
  a request with no `Authorization` header is rejected with
  `{"error":"JWT Token is required."}` (see `reject.jwt-missing.*`).
- **`X-Client-Id: ddk-model-wallet`** selects the wallet. The value is the
  wallet's system-generated `clientId` (read from the omni API response — see
  `wallet.definition.json`; here it mirrors the wallet name, but always read it
  from the response, do not assume the format). The gateway echoes the match back
  as the response header **`x-model-wallet-selected: ddk-model-wallet`**.
- **No `client_id`/`client_secret` request headers are sent** and the call still
  succeeds — confirming the default **Client ID Enforcement** and **DataWeave
  Headers Transformation** policies are disabled on a wallet proxy, and the caller
  is identified purely from the JWT + `X-Client-Id`.
- **Claim path.** The LLM Proxy Core Policy's Client ID reads
  `#[authentication.properties.claims.client_id]` — the `client_id` claim the JWT
  Validation policy publishes after validating the token. The wallet's
  `predicates` (`group=ddk` AND `client_id=ddk-model-wallet-client`) are matched
  against the token's claims; both are present in `jwt.claims.json`.

## Files

- `request.success.http` — the exact request line + headers (JWT redacted) + body.
  Note `Authorization: Bearer …` + `X-Client-Id: ddk-model-wallet`, and **no**
  `client_id`/`client_secret` header.
- `jwt.claims.json` — decoded payload of the RS256 token from the DDK Keycloak IdP
  (realm `master`). The raw JWT is **never stored**; opaque `jti`/`sub`/`sid`
  redacted, `exp`/`iat` shown as placeholders. Load-bearing claims: `client_id`,
  `group` (wallet predicates) and `aud` (JWT Validation audience boundary).
- `wallet.definition.json` — the wallet object from the omni apim-proxy API
  (`GET …/model-wallet`), UUIDs redacted. Shows `clientId`, `predicates`, and the
  routed `modelId` the budget counts against.
- `responses.success.body.json` — HTTP **200** body from
  `POST /ddk-model-wallet/chat/completions`. OpenAI **Chat Completions** object,
  returned verbatim, incl. the `usage` token block used for cost/budget attribution.
- `responses.success.headers.txt` — 200 response headers (`set-cookie` stripped).
  Note `server: Anypoint Flex Gateway`, **`x-model-wallet-selected`**, the
  `x-llm-proxy-*` governance headers (routing-type/provider/model + the
  `…-cost-per-1m` pair + `…-request-success`), `x-envoy-decorator-operation`
  (`api-instance-21186246.<envId>.svc`), `x-correlation-id`, and passed-through
  OpenAI `x-ratelimit-*` / `x-request-id`.
- `reject.jwt-missing.{body.json,headers.txt}` — HTTP **400** when no
  `Authorization: Bearer` header is present. Anypoint policy envelope
  `{"error":"JWT Token is required."}` + `www-authenticate: Bearer`. **No**
  `x-llm-proxy-*` routing headers (rejected before routing).
- `reject.jwt-invalid.{body.json,headers.txt}` — HTTP **401** for a malformed /
  unverifiable / expired Bearer token. Envelope `{"error":"Invalid token."}` +
  `www-authenticate: Bearer`. Discriminator vs. the missing-token 400 is the
  status code + body string; both carry `www-authenticate: Bearer` (distinct from
  the CIE path's `www-authenticate: Client-ID-Enforcement` in `../llm_proxy/`).

## Enforcement matrix (verified 2026-09-21)

| Case | Sent | Status | Body |
|---|---|---|---|
| success | valid JWT (`aud` incl. `ddk-model-wallet`) + `X-Client-Id` | **200** | OpenAI completion + `usage` |
| missing JWT | `X-Client-Id`, no `Authorization` | **400** | `{"error":"JWT Token is required."}` |
| invalid JWT | garbage/expired Bearer + `X-Client-Id` | **401** | `{"error":"Invalid token."}` |

(A validly-signed token whose `aud` does **not** include the wallet name is also a
`401` audience rejection — see the `ddk-configure-llm-proxy-model-wallet` skill's
matrix; not re-captured here.)

## Auth / secrets

The JWT is minted per-call from the DDK reference Keycloak IdP and is **never
written to disk** — only its decoded, redacted claims are stored. No Anypoint
credential, provider API key, or Bearer token appears in any file here;
`set-cookie` was stripped on capture. The wallet caps spend but does **not** grant
or deny proxy access — access is gated by JWT validity + audience.

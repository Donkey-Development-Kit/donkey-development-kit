# Packaged A2A ingress captures (#1397)

`a2a_policies/` contains immutable copies of ten response files and the original
provenance README from `tests/fixtures/anypoint/a2a_policies/` (#1393). Only the
v1 Azure prompt-shield block, PII rejection and token-rate rejection are served,
each with separate `SendMessage` and `SendStreamingMessage` captures. The
original README is byte-identical historical evidence: its "test-only" statement
describes the capture-time state, superseded for this subset by this packaging.
No new policy capture, auth-code simulation or outbound A2A simulation is added.

The existing `fixtures.SHAPES` table resolves these files through `fixture_bytes`;
the served `fixtures.lock` pins their SHA-256 hashes. Tests compare the packaged
files and hashes to the original test-tree files and `tests/fixtures/fixtures.lock`.
Unlike the packaged MCP evidence in #1571, these are served shapes, so they use
the served lock and the existing `python -m donkey_kit.simulator.fixtures --relock`
command rather than a separate capture manifest. The wheel takes nothing from
`tests/`: all runtime resources are under `src/donkey_kit/simulator/_fixtures/`.

## Replay boundaries

- `injection:on-pattern=...` selects the captured Azure `prompt_shield` HTTP 403,
  not the LLM proxy's injection HTTP 400. The shared safe-header filter keeps the
  captured `x-flex-azure-guardrails-{action,phase,reason}` discriminator trio.
  Existing classification returns `ContentSafetyBlocked`.
- `pii_block:every=N` selects the captured v1 PII HTTP 403. Its `ErrorInfo`
  envelope has no unambiguous policy identity, so existing classification remains
  `ToolInvocationError`, not `PIIDetected`. The raw synthetic email findings stay
  in the captured bytes; do not log or render them as exception diagnostics.
- `request_limit` is a request-count trigger for replay, not a reproduction of
  the gateway's token accounting. Its configured limit/window determine *when*
  the bare 429 is replayed, not the captured header values. The rejection keeps
  `x-token-limit: 1`, `x-token-remaining: 1`, `x-token-reset: 60000` and no
  `retry-after`; classification returns `TokenBudgetExceeded`. Passing calls get
   no synthesized rate headers: accepted v1 headers have not been captured.
   The test-only accepted v0.3 send capture does not establish v1 replay behavior.
- Streaming refusals are request-phase HTTP JSON or empty responses, not SSE
  error events. Passing responses, including streams, retain the agent's bytes.
- Response JSON-RPC IDs remain the captured string `"1393"`, even for a different
  incoming ID. The loader does not reserialize bodies or invent a gateway ID
  guarantee. This is fault replay, not a request-correlating protocol emulator.
- With the stock v1 `a2a-sdk` 1.2.2 JSON-RPC transport, send and stream generate UUID
  request IDs. The tested client raises `A2AClientError` caused by
  `httpx.HTTPStatusError` on these HTTP 403/429 responses before parsing the
  JSON-RPC error or checking response IDs. This establishes HTTP refusal
  handling only, not successful ID correlation, typed DDK refusals in a stock
  client, or compatibility with clients that require matching IDs.
- `replay_headers` applies its existing semantic allow-list, dropping transport
  framing and CDN/server metadata. Response framing is generated locally and
  `x-donkey-simulator: true` is added to every response, never to capture files.

For an intentional future re-capture, update and review the original evidence,
copy the exact bytes here and run `--relock`. Other #1393 evidence (schema,
service-unavailable, v0.3, quality and authorization-code) remains test-only.

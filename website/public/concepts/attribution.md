# Attribution & cost

The core value of governed model access is **per-agent attribution**: knowing
which agent/app spent which tokens, without every team rolling their own
bookkeeping.

## How attribution works (live-verified)

For a directly-called ingress LLM proxy, the **attribution unit is the
`client_id` credential**. The calling agent/app is a registered Anypoint client
application, enforced by the `client-id-enforcement` policy — it is *not* a
bespoke header the SDK invents.

- **Identity** rides on the verified `client_id` / `client_secret` request-header
  pair (also your auth — see [Governance](https://donkey-development-kit.github.io/donkey-development-kit/concepts/governance.md)).
- **The gateway emits identity/telemetry on the response**: an
  `x-envoy-decorator-operation` carrying the API-instance id + environment id, an
  `x-correlation-id`, and `x-llm-proxy-*` routing headers.
- **Token usage** for cost attribution comes from the response `usage` block
  (`input_tokens`, `output_tokens`, `total_tokens`, plus the detail counts
  `cached_tokens` / `cache_write_tokens` / `reasoning_tokens`), passed through
  verbatim from the provider and surfaced per-call on `donkey.last_call` when
  the response passes through the SDK's shared HTTP client. See
  [when `last_call` is unavailable](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md#when-last_call-is-unavailable).

## Optional attribution metadata

You can attach human-readable attribution that the SDK surfaces on telemetry:

```bash
export DONKEY_APP_NAME="checkout-agent"
export DONKEY_BUSINESS_GROUP="payments"
```

## Correlation IDs across a run

Bind a **run** id once and every governed call inside the block shares it — on
the wire, on the span, and on any exception — so a run's calls trace together:

```python
async with donkey.run(id="order-1234"):
    ...  # calls in here share the run id
```

The run id becomes the `X-Correlation-Id` request header, the span's
`donkey.correlation_id`, and `DonkeyError.correlation_id`; each individual
request additionally carries a per-call `X-Donkey-Request-Id` that surfaces on
`DonkeyError.call_id`. See [Observability](https://donkey-development-kit.github.io/donkey-development-kit/concepts/observability.md#grouping-a-run-the-run-id-on-the-span-the-call-id-on-the-error)
for the full two-id model.

  **Google ADK and CrewAI caveat.** LiteLLM owns their transports, so the SDK
  can't inject its HTTP client — the correlation id is per-client, not per-run.
  These are documented, asserted conformance exemptions, not silent gaps.

  The **agent→agent egress** attribution header (`x-anypoint-api-instance-id`) is
  a separate telemetry path for component-to-component traffic, not needed for
  direct LLM proxy calls. The business-group *request* header shape is not
  surfaced in the direct-proxy path and remains unverified.

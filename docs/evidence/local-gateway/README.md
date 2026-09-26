# Local Mode verification — 2026-09-26

Issue #650, a partial-evidence slice of #65. Flex Gateway **1.14.0**, Linux amd64 under Docker 29.8.0 on
macOS arm64. Image digest:
`sha256:b21e1d901492bc2d2604848eff63e8c887c44a599f749d68212c5d19471452d1`.
Base DDK commit: `ee56f8112f633b0368629376b59fcf5fc1f1f2d1`.

## What actually ran

A disposable Local Mode gateway was registered using a connected app in Sandbox
(org `4ac1188d-b7b2-469d-9ec5-e68c760bf874`, environment
`67ebd3a4-afb7-4f6f-be27-3c4a8611be15`). The resulting registration was mounted
read-only into a stock container. Both runtime containers used `--network none`;
registration itself necessarily contacted the control plane. No existing gateway
or API instance was modified. No registration keys, certificates, or consumer
credentials are included in this evidence.

1. **Without registration:** the image starts its processes but rejects its
   extensions with `the registration configuration is missing`. Its readiness
   probe fails with `Gateway not started: no API instances registered`.
   See [unregistered.log](unregistered.log) and
   [unregistered-readiness.txt](unregistered-readiness.txt).
2. **With registration:** the documented `ApiInstance` config activates a local
   listener. The deliberately absent upstream produces the captured
   [503 response](baseline.response.http), and readiness passed before adding the
   intentionally unavailable policy probes. This confirms local API processing
   with the registration artifact, not a functioning LLM provider.
3. **Model Proxy / MCP probes:** references to `llm-proxy-core`,
   `model-based-routing`, `openai-transcoding-policy`, and `mcp-support` are
   rejected as missing extensions. These names come from the existing verified
   ledger's deployed policy stack, not guessed alternate Local Mode names.
   See [registered.log](registered.log). This is a negative result for the stock
   image/configuration only. It does not prove no separately installed extension
   could implement an equivalent capability. A complete MCP Bridge and Model
   Proxy deployment remains unverified.
4. **Injection Protection:** `injection-protection-flex` loads and rejects a
   synthetic XSS body with HTTP 400 and `x-injection-protection: blocked`.
   The [request](injection.request.http) and [response](injection.response.http)
   are unmodified HTTP bytes captured through the container's TCP socket.
   The response body is 76 bytes. This is a real **local gateway** policy capture,
   not an Anypoint-hosted LLM-proxy capture; it does **not** close #253 or change §4.

The runtime logs `Mode=offline` for the registration created with `--mode=local`.
Its failed usage-metric submission under network isolation is visible in the log.
A short isolated test is not evidence of indefinite disconnected operation or
of any license/entitlement exemption.

## Policy portability table

| Policy from §6 / capture | Observed in stock 1.14.0 Local Mode | Limit |
|---|---|---|
| `llm-proxy-core` | Extension not found | Model Proxy operation remains unverified |
| `model-based-routing` | Extension not found | No routing behavior established |
| `openai-transcoding-policy` | Extension not found | No transcoding behavior established |
| `mcp-support` | Extension not found | No MCP session or Bridge was exercised |
| `cors` | Reference resolves without an extension-not-found error | Bare reference only; CORS behavior not tested |
| `dataweave-headers-transformation` | Reference resolves without an extension-not-found error | Bare reference only; transformation behavior not tested |
| `client-id-enforcement` | Reference resolves without an extension-not-found error | Bare reference only; consumer validation not tested |
| `header-injection` | Reference resolves without an extension-not-found error | Bare reference only; header behavior not tested |
| `injection-protection-flex` | Loaded and blocked XSS with HTTP 400 | Positive local behavior and raw capture included |

The official [gateway policy comparison](https://docs.mulesoft.com/gateway-home/)
and individual policy pages explicitly exclude Local Mode for
[MCP Support](https://docs.mulesoft.com/gateway/latest/policies-included-mcp-support),
[LLM token rate limiting](https://docs.mulesoft.com/gateway/latest/policies-included-llm-token-rate-limit),
[LLM PII Detection](https://docs.mulesoft.com/gateway/latest/policies-included-llm-pii-detection),
and [Regex Prompt Guard](https://docs.mulesoft.com/gateway/latest/policies-included-regex-prompt-guard).
These are documentation findings, distinct from the runtime observations above;
the untested policies are not labelled live-verified. Local support for Injection
Protection is also [documented](https://docs.mulesoft.com/gateway/latest/policies-included-injection-protection).

## Reproduce

Committed configs: [`python/tests/fixtures/local_gateway/config/`](../../../python/tests/fixtures/local_gateway/config/).
The baseline routing syntax follows the official
[Local Mode configuration tutorial](https://developer.mulesoft.com/tutorials-and-howtos/anypoint-flex-gateway-local-mode-config-file/).
The Injection Protection config uses its official policy reference/fields.
The negative Model Proxy/MCP probes deliberately reference the recorded deployed
policy names and preserve the resulting errors.

Install the repo's `.[dev,llm,cli]` extras, make the pinned Docker image available,
and run from `python/`:

```bash
pytest -q -m local_gateway tests/conformance/test_flex_local_mode.py
DDK_FLEX_REGISTRATION=/absolute/path/to/registration.yaml \
  pytest -q -m local_gateway tests/conformance/test_flex_local_mode.py
```

With no Docker daemon or image, all three tests clean-skip. Without registration,
the unregistered negative test runs and the two registered tests clean-skip.
When prerequisites exist, failures are assertions, not skips. The tests create
isolated containers with unique names and remove them in fixture teardown. They
never pull an image, register a gateway, or call the control plane.

Create a dedicated artifact through the documented
[Local Mode connected-app registration flow](https://docs.mulesoft.com/gateway/latest/local-reg-run-app).
The tested image supports `flexctl registration create --mode=local`; credentials
and organization/environment IDs must be provided through your secret-handling
workflow. Registration output is sensitive and must remain outside the repository.

## Dev-loop / CI decision

Keep DDK's labelled pure-Python simulator as the default contributor experience.
Real Flex Local Mode is an opt-in integration surface requiring a provisioned
registration artifact. An authorized CI job can mount that artifact from a secret;
public forks without it can run the unregistered test and the existing simulator.
No live support claims or SDK verification guards change in this PR. The remaining
Model Proxy/MCP Bridge deployment questions stay open on #65.

## Validation

- Real pinned image and dedicated Sandbox registration: [3 passed](gate-local-docker.txt).
- Docker inaccessible in the restricted test environment: [3 clean skips](gate-docker-unavailable.txt).
- [Standard pytest](gate-pytest.txt), [mypy](gate-mypy.txt), [Ruff](gate-ruff.txt),
  and [import boundaries](gate-imports.txt) passed.

The two manually launched runtime containers and the disposable control-plane
registration were removed after the probes. The pytest fixtures removed their
own uniquely named containers.

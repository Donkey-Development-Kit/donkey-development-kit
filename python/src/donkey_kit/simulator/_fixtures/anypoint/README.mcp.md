# Packaged MCP captures (partial #1391)

This is the user-approved fixture-only slice of
[#1391](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/1391)
(BG section 1.4 / 2.2). It packages immutable copies of the 13 envelopes in
`mcp_policies/` and two header/body pairs in `mcp_access/`. The original
captures remain under `tests/fixtures/anypoint/` for existing consumers.
Both directory READMEs are byte-identical provenance records, including policy
versions, capture procedures, redaction boundaries and incomplete coverage.

`mcp-captures.lock` pins SHA-256 hashes for all 19 copied files, including
provenance, matching the corresponding entries in `tests/fixtures/fixtures.lock`.
It is separate from the simulator's `fixtures.lock`: these captures are **not
served shapes**. No `SHAPES` entries, `simulate()` scenarios, bound-tool
`simulate()` transport injection, or `donkey mock` registration are added.
In particular, registering an MCP refusal as an LLM simulation would inject
the wrong protocol into a model call. No runtime classification changes or
new public APIs are part of this slice.

## Offline raw HTTP replay

Use the existing `fixture_bytes` resource API, which also works from an installed
wheel. Envelope `body` strings are decoded once and encoded as UTF-8, **not
JSON-reserialized**; SSE framing and JSON-RPC IDs remain captured evidence.
For access captures, read the header/body pair with `fixture_bytes`, then use
the existing `parse_headers` and `parse_status` helpers. `Fixture` and
`replay_headers` supply the existing safe-header replay convention. Restore
`content-type` explicitly and add `x-donkey-simulator: true` to every replay;
never add that honesty stamp to the immutable capture itself.

```python
import json

import httpx

from donkey_kit.simulator.fixtures import Fixture, fixture_bytes, replay_headers

capture = json.loads(fixture_bytes("anypoint/mcp_policies", "pii-request.json"))
fixture = Fixture(
    shape="mcp-capture:pii-request",
    status=capture["status"],
    headers=capture["headers"],
    body=capture["body"].encode("utf-8"),
    content_type=capture["headers"]["content-type"],
)

def respond(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        fixture.status,
        headers={
            **replay_headers(fixture),
            "content-type": fixture.content_type,
            "x-donkey-simulator": "true",
        },
        content=fixture.body,
    )

with httpx.Client(transport=httpx.MockTransport(respond)) as client:
    response = client.post("https://gateway.example/mcp/")
    assert response.content == fixture.body
```

This is raw `httpx.MockTransport` replay, not a framework MCP session simulator:
it does not negotiate sessions, route methods, rewrite request IDs, inject into
bound framework tools, or claim end-to-end framework support.

## Evidence boundaries

PII has structured policy/reason/phase markers; access denials have the captured
`-32008` / `Unable to access` signature. Existing `jsonrpc_refusal` and
`tool_access_denial` matchers can be tested offline against those bytes.
Generic schema errors and poisoning findings **cannot safely identify a gateway
policy**: error codes/messages alone can also come from an MCP server. Schema
`data` even echoes a rejected value. Poisoning here means descriptor drift, not
proven malicious content. Ordinary `result.isError: true` is a failed tool
result, not a policy refusal. These negatives must remain untyped by the RPC
matcher rather than inventing a discriminator.

Despite its filename, `pii-response-sse.json` is **HTTP 403 application/json**,
not an HTTP 200 SSE error event. The baseline upstream was SSE; the rejection
was not. Other SSE captures retain their `event: message` / `data:` framing.

Capture evidence is recorded in
[#1384](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/1384)
and [#303](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/303),
and in the verification ledger's MCP policy rows. The missing structured
schema/poisoning policy identity is the upstream gap
[#1401](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/1401).
Remaining capture and payload-transform coverage stays with #1384 and
[#1390](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/1390);
packaging these files does not close those gaps or the remainder of #1391.

For an intentional future re-capture, review the evidence and update the original
capture and its test-only lock, then copy the exact bytes and matching hashes
here. The existing `--relock` command does not regenerate `mcp-captures.lock`.

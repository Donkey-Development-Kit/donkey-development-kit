# LangGraph support-triage demo — Scenario A

Deep adapter — conformance-tested (BG §1.8) — and the Phase-1 acceptance
artefact (#199): Scenario A end to end, from `pip install` to a drafted reply,
**against the local simulator only** — no Anypoint credentials, no real gateway.

**What this shows.** A real, two-node LangGraph app (`prepare` → `call_model`,
a compiled `StateGraph`) — not a bare model call — built against the governed
Agent Fabric LLM proxy. `build(donkey)` is a **conformance-able agent factory**:
it returns an object with an awaitable `run(text)`, which is exactly the shape
the customer-facing conformance plugin drives (and the SDK's own
`tests/conformance/test_langgraph_conformance.py` runs the four scenarios
against). `main()` then runs the full Scenario A:

- **No gateway.** It boots the local gateway simulator (BG §1.4) in-process on
  an ephemeral port and points the SDK at it. The simulator ignores auth, so the
  credentials are throwaway placeholders.
- **The PII masking branch.** The simulator runs the `pii_block:every=5`
  scenario (#188): every 5th `POST /responses` is served the `pii-detected`
  403. The demo triages five support tickets, so the fifth — which we fill with
  an SSN — is blocked, surfacing out of `graph.ainvoke` as a typed `PIIDetected`
  (not a framework-wrapped generic error). *The simulator triggers on the
  **count**, not by scanning content — it is a fixture replay, not a PII
  detector; the SSN just makes the blocked ticket read true. The gateway does
  the real detection.*
- **Spans to a local OTLP collector.** `collector.py` is a ~40-line stdlib
  OTLP/HTTP receiver. Pointing `OTEL_EXPORTER_OTLP_ENDPOINT` at it lights up the
  SDK's zero-config export (#194) automatically — every governed call's span
  (refusals included) flows over the wire and is counted.

It is timed in CI (the `langgraph-demo` job) so this first-run experience can
never silently rot. On startup you'll see two `UnverifiedValueWarning` lines
about the correlation header names — that is the SDK's honest verification discipline signal that
those header names aren't yet confirmed against a live proxy, not an error.

The model itself is a *native* `langchain_openai.ChatOpenAI` pointed at the
proxy — correct base URL (no `/v1`), `client_id`/`client_secret` header auth (not
bearer), attribution headers, the SDK's shared transport (retry/telemetry
hooks), and `use_responses_api=True` (the proxy's live-verified `/responses`
data plane). It's LangGraph/LangChain's own class, not a wrapper, so it drops
straight into any graph or chain.

The graph demonstrates the two deep-adapter guarantees this example exists to
prove (#198):

- **Correlation reaches every node for free (AC2).** `prepare` logs
  `current_correlation_id()` without it ever being threaded through graph
  state — a run id bound with `donkey.run(id=…)` shows up there because
  LangGraph runs nodes on context-copying `asyncio` tasks (#195).
- **Typed refusals inside a node (AC3).** `call_model` wraps `model.ainvoke`
  in `typed_refusals()`, so a proxy refusal surfaces out of `graph.ainvoke`
  as the SDK's typed `DonkeyError` (e.g. `PIIDetected`), not a
  framework-wrapped generic error.

> 📖 **Prefer reading to running?** The canonical walkthrough — install,
> configure, and the manual equivalent — is in the docs:
> **[LangGraph](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/langgraph)**.
> This README duplicates the runnable essentials on purpose so you can run it in
> place; if the two ever differ, the docs page is canonical.

## Run (no gateway, no credentials)

```bash
pip install "donkey-kit[langgraph,local,otel]"

# Run as a module (it imports the bundled collector as a sibling):
python -m examples.langgraph.main
```

- `langgraph` — `langchain_openai.ChatOpenAI` + `langgraph`.
- `local` — the local gateway simulator (`starlette` + `uvicorn`).
- `otel` — the OpenTelemetry SDK + OTLP exporter, so spans reach the collector.

Expected output: four drafted replies, ticket 5 **blocked** by `PIIDetected`,
the budget remaining, and the span count the local OTLP collector received. The
whole run takes ~1–2 seconds.

## Verify it against conformance

The same `build` factory the demo uses is what the customer-facing conformance
plugin drives — run the four scenarios against it exactly as a customer would
against their own agent:

```bash
pytest --donkey-conformance --agent=examples.langgraph.main:build
```

## The manual equivalent

The factory call is equivalent to building `ChatOpenAI` yourself with the
governed connection values (BG §1.8):

```python
import httpx
from langchain_openai import ChatOpenAI

model = ChatOpenAI(
    model="gpt-4o",
    base_url=DONKEY_LLM_PROXY_URL,
    api_key="unused",  # the proxy enforces client_id/client_secret headers instead
    default_headers={
        "client_id": DONKEY_LLM_PROXY_CLIENT_ID,
        "client_secret": DONKEY_LLM_PROXY_CLIENT_SECRET,
    },
    http_async_client=httpx.AsyncClient(...),  # your own transport, retries, hooks
    max_retries=0,
    use_responses_api=True,  # the proxy's live-verified data plane is /responses
)
```

The factory (`donkey_kit.integrations.langgraph.chat_model`) fills in
`base_url`, `api_key`, `default_headers`, `http_async_client`, and
`use_responses_api` from one governed config source and gives you the SDK's
shared transport (with its retry policy and telemetry hooks) for free. Pass
`use_responses_api=False` only if a deployment exposes chat-completions instead
— `/responses` is the endpoint verified against a live proxy.

## Links

- LangGraph docs: https://langchain-ai.github.io/langgraph/
- LangChain `ChatOpenAI` docs: see the framework's official documentation

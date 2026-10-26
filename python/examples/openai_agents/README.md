# OpenAI Agents SDK support-triage demo — Scenario A

Deep adapter — conformance-tested (BG §1.8), promoted by Phase 1 demand (#223).
The counterpart of `examples/langgraph`: Scenario A end to end, from
`pip install` to a drafted reply, **against the local simulator only** — no
Anypoint credentials, no real gateway — through the Agents SDK's own
`Runner.run`.

**What this shows.** A real `agents.Agent`, built on the governed Agent Fabric
LLM proxy and run by `agents.Runner`. `build(donkey)` is a **conformance-able
agent factory**: it returns an object with an awaitable `run(text)`, which is
exactly the shape the customer-facing conformance plugin drives (and the SDK's
own `tests/conformance/test_openai_agents_conformance.py` runs the four
scenarios against). `main()` then runs the full Scenario A:

- **No gateway.** It boots the local gateway simulator (BG §1.4) in-process on
  an ephemeral port and points the SDK at it. The simulator ignores auth, so the
  credentials are throwaway placeholders.
- **The PII masking branch.** The simulator runs the `pii_block:every=5`
  scenario (#188): every 5th `POST /chat/completions` (the route
  `OpenAIChatCompletionsModel` calls) is served the `pii-detected` 403. The demo
  triages five support tickets, so the fifth — which we fill with an SSN — is
  blocked. `Runner.run` is wrapped in `typed_refusals()`, so it surfaces as a
  typed `PIIDetected`, not the OpenAI client's generic `PermissionDeniedError`.
  *The simulator triggers on the **count**, not by scanning content — it is a
  fixture replay, not a PII detector; the SSN just makes the blocked ticket read
  true. The gateway does the real detection.*
- **Spans to a local OTLP collector.** `collector.py` is the same stdlib
  OTLP/HTTP receiver the LangGraph demo bundles. Pointing
  `OTEL_EXPORTER_OTLP_ENDPOINT` at it lights up the SDK's zero-config export
  (#194) — every governed call's span (refusals included) flows over the wire
  and is counted. The Agents SDK's own tracing is off (`RunConfig(
  tracing_disabled=True)`): its default exporter posts to OpenAI, not the proxy.
- **Correlation reaches the agent for free.** The agent's dynamic
  `instructions` log `current_correlation_id()` without it being passed through
  the run context: `Runner.run` resolves instructions and runs `function_tool`s
  inside the caller's context, and every model request carries the run id on
  the shared transport (`tests/unit/test_openai_agents_correlation.py`).

It is timed in CI (the `openai-agents-demo` job) so this first-run experience
can never silently rot.

The model is a *native* `agents.OpenAIChatCompletionsModel` whose `AsyncOpenAI`
client the adapter builds itself — correct base URL (no `/v1`),
`client_id`/`client_secret` header auth (not bearer), attribution headers, and
the SDK's shared transport (retry/telemetry hooks). It is the Agents SDK's own
class, not a wrapper, so it drops straight into `agents.Agent(model=...)`.

**No asserted exemption.** An `@donkey.tool(approval="required")` wrapped with
`donkey_kit.integrations.openai_agents.function_tool` pauses `Runner.run` on the
SDK's own `needs_approval`; `pending_approvals`, `approve` and `reject` record the
decision on the `RunState` before the run resumes (#213).

> 📖 **Prefer reading to running?** The canonical walkthrough is in the docs:
> **[Support triage with the OpenAI Agents SDK](https://docs.donkey-kit.dev/scenarios/support-triage-agents-sdk)**,
> and the framework page is
> **[OpenAI Agents SDK](https://docs.donkey-kit.dev/frameworks/openai)**.
> This README duplicates the runnable essentials on purpose so you can run it in
> place; if the two ever differ, the docs page is canonical.

## Run (no gateway, no credentials)

```bash
pip install "donkey-kit[llm,openai-agents,local,otel]"

# Run as a module (it imports the bundled collector as a sibling):
python -m examples.openai_agents.main
```

- `openai-agents` — the `openai-agents` package (`agents.Agent`, `Runner`).
- `local` — the local gateway simulator (`starlette` + `uvicorn`).
- `otel` — the OpenTelemetry SDK + OTLP exporter, so spans reach the collector.

Expected output: four drafted replies, ticket 5 **blocked** by `PIIDetected`,
the budget remaining, and the span count the local OTLP collector received. The
whole run takes about a second.

## Verify it against conformance

The same `build` factory the demo uses is what the customer-facing conformance
plugin drives — run the four scenarios against it exactly as a customer would
against their own agent:

```bash
pytest --donkey-conformance --donkey-agent=examples.openai_agents.main:build
```

## The manual equivalent

The factory call is equivalent to building the model yourself with the governed
connection values (BG §1.8):

```python
import httpx
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel

m = OpenAIChatCompletionsModel(
    model="gpt-4o",
    openai_client=AsyncOpenAI(
        base_url=DONKEY_LLM_PROXY_URL,
        api_key="unused",  # proxy enforces client_id/client_secret headers
        default_headers={
            "client_id": DONKEY_LLM_PROXY_CLIENT_ID,
            "client_secret": DONKEY_LLM_PROXY_CLIENT_SECRET,
        },
        http_client=httpx.AsyncClient(...),  # your own transport, retries, hooks
        max_retries=0,
    ),
)
```

The factory (`donkey_kit.integrations.openai_agents.model`) fills in the
`AsyncOpenAI` client, headers, and the SDK's shared transport from one governed
config source.

## Links

- OpenAI Agents SDK docs: https://openai.github.io/openai-agents-python/

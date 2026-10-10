# Google ADK

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

Google ADK with `donkey.adk.model("…")`, a `LiteLlm` model. LiteLLM calls
the proxy's `/chat/completions` route. It sends through the SDK's shared HTTP client, so the credentials and the run id
go on the wire, but LiteLLM raises its own errors, so the SDK does not convert
a refusal to a **typed error** here; the `openai` error is in the `__cause__`
chain. Script 02 exists to show that gap.

Both scripts need a live gateway; there is no offline ADK script.

| # | Script | Shows | Needs |
| --- | --- | --- | --- |
| 01 | `basic-gw.py` | An ADK `Agent` through an `InMemoryRunner` | Proxy credentials |
| 02 | `refusal-live.py` | A PII refusal surfacing as LiteLLM's `APIError` | Proxy + PII policy |

## Install

Follow the [examples setup](https://docs.donkey-kit.dev/examples.md#setup) first, then:

```bash
python -m pip install -e "../donkey-development-kit/python[llm,adk]"
set -a; source .env.local; set +a
```

## 01 — An ADK agent

```bash
python "demos/human-made/adk/01 - basic-gw.py"
```

```python
donkey = Donkey.from_env()
agent = Agent(name="greeter", model=donkey.adk.model("gpt-4o"), instruction="Answer in one short sentence.")

events = asyncio.run(InMemoryRunner(agent=agent).run_debug("Say hello in exactly three words.", quiet=True))

print(events[-1].content.parts[0].text)
print("total tokens", events[-1].usage_metadata.total_token_count)
print("last_call   ", donkey.last_call.status.value, donkey.last_call.surface)
```

**You should see:** a one-sentence answer, `total tokens` from the last
event's `usage_metadata`, and `last_call unobserved …`. The record is set in
the `Runner`'s own task, not where the script reads it, so the script's read
is a cold one. Read it
in an `after_model_callback` instead, as shown under
[Native Gemini](https://docs.donkey-kit.dev/frameworks/adk.md#native-gemini). LiteLLM also logs a
provider-list banner, which is harmless.

## 02 — A refusal that is not typed

```bash
python "demos/human-made/adk/02 - refusal-live.py"
```

A PII prompt against a proxy with the PII policy applied. LiteLLM keeps the
status code and the message but drops the response headers, so `classify()`
has nothing to read. The script therefore catches LiteLLM's own error:

```python
try:
    asyncio.run(InMemoryRunner(agent=agent).run_debug(PII_PROMPT, quiet=True))
    print("NO REFUSAL")
except litellm.exceptions.APIError as err:
    print(type(err).__name__, err.status_code)
    print(str(err).splitlines()[0])
```

**Needs:** `llm-pii-detection-policy` with `Email` and action `Reject`. **You
should see:** `APIError 403` and the first line of the proxy's message — **not**
`PIIDetected`. Without the policy it prints `NO REFUSAL`.

  If you need typed refusals with ADK, use
  `donkey.adk.gemini("gemini-2.5-flash")` on a `Format=Gemini` proxy and
  `classify()` its error (see [Native Gemini](https://docs.donkey-kit.dev/frameworks/adk.md#native-gemini)).
  Otherwise prefer a framework path where the SDK owns the transport, such as
  [OpenAI](https://docs.donkey-kit.dev/examples/openai.md) or [LangGraph](https://docs.donkey-kit.dev/examples/langgraph.md). A `404`
  here means the proxy's upstream has no `/chat/completions` route.

**Learn more:** [Google ADK](https://docs.donkey-kit.dev/frameworks/adk.md)

**Source:**
[`demos/human-made/adk/`](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/human-made/adk)

# Microsoft Agent Framework

`donkey.agent_framework.chat_client("…")` builds an `OpenAIChatClient`
(verified against 1.19.0, where the keyword is `model=`) that calls
`/responses`. Only `default_headers` are handed over, so the proxy sees your
credentials but the SDK does not own the transport: there is **no run id and
no `last_call`**. Refusals still come back typed — Agent Framework wraps the
openai error in `ChatClientException`, and the response rides on `__cause__`.

| # | Script | Shows | Needs |
| --- | --- | --- | --- |
| 01 | `basic-gw.py` | An `Agent` on the governed chat client | Proxy credentials |
| 02 | `typed-refusals-live.py` | `PIIDetected`, `UpstreamRequestError`, `AuthError` | Proxy + PII policy |
| 03 | `start-gateway.py` | Two tickets over the local simulator | `[local]` |

## Install

Follow the [examples setup](https://donkey-development-kit.github.io/donkey-development-kit/examples.md#setup) first, then:

```bash
python -m pip install -e "../donkey-development-kit/python[llm,local,agent_framework]"
set -a; source .env.local; set +a
```

## 01 — A governed agent

```bash
python "demos/human-made/agent-framework/01 - basic-gw.py"
```

```python
donkey = Donkey.from_env()
agent = Agent(
    client=donkey.agent_framework.chat_client("gpt-4o"),
    name="greeter",
    instructions="Answer in one short sentence.",
)

result = asyncio.run(agent.run("Say hello in exactly three words."))
print(result.text)
print("total tokens", result.usage_details["total_token_count"])
print("last_call   ", donkey.last_call.status.value, donkey.last_call.surface)
```

**You should see:** the reply, `total tokens` from Agent Framework's
`usage_details`, and `last_call unavailable …` — the honest answer when the SDK
only supplied headers. Read usage from the framework on this path.

## 02 — Typed refusals, live

```bash
python "demos/human-made/agent-framework/02 - typed-refusals-live.py"
```

Three cases, each with its own `Donkey`: a contact record for `PIIDetected`, a
model that does not exist for `UpstreamRequestError`, and wrong credentials
for `AuthError`. The one Agent Framework-specific line is where the response
is read from:

```python
try:
    asyncio.run(agent.run(prompt))
    print(name, "NO REFUSAL")
except ChatClientException as err:
    error = classify(err.__cause__.response)
    print(name, "->", type(error).__name__, getattr(error, "entities", None))
```

**Needs:** `llm-pii-detection-policy` with `Email` and action `Reject` for the
first case. **You should see:** `<case> ->  <entities>` per case, or
`<case> NO REFUSAL`.

## 03 — Two tickets over the local simulator

```bash
python "demos/human-made/agent-framework/03 - start-gateway.py"
```

No gateway and no credentials. `start_gateway()` with `pii_block:every=2`:
the first ticket gets the simulator's canned completion, the second is the
captured PII 403, unwrapped from `ChatClientException` and classified.

```python
gw = start_gateway()
gw.set_scenarios("pii_block:every=2")
donkey = Donkey(DonkeyConfig(llm_proxy_url=gw.url, llm_proxy_client_id=..., llm_proxy_client_secret=...))

for ticket in TICKETS:
    agent = Agent(client=donkey.agent_framework.chat_client("gpt-4o"), instructions="Reply in one sentence.")
    try:
        print("ok     ", asyncio.run(agent.run(ticket)).text[:60])
    except ChatClientException as err:
        error = classify(err.__cause__.response)
        print("refused", type(error).__name__, error.entities)
```

```text
ok      A sleepy unicorn named Luma painted soft silver stars across
refused PIIDetected ['Email']
requests 2
```

  `TypeError … model_id` means an older Agent Framework; the scripts target
  the 1.19.0 `model=` keyword.

**Learn more:** [MS Agent Framework](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/agent-framework.md)

**Source:**
[`demos/human-made/agent-framework/`](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/human-made/agent-framework)

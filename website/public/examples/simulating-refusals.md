# Simulating refusals

Every agent has an `except PIIDetected:` branch that has never executed.
Getting a real gateway to refuse on demand means finding a prompt that trips a
live policy — slow, flaky, and not something you can put in CI.
`donkey.simulate()` swaps a fixture-returning transport onto the client for the
next N calls, so the branch runs against the same captured refusal a real
gateway sent, with no network. For a client that does not use the SDK at all,
`donkey mock --scenario` scripts the running simulator the same way.

| Example | Shows | Needs |
| --- | --- | --- |
| Narrative demo 04 | `simulate()` for each refusal, `times=`, what it refuses to fake, the same injection through LangChain's `ChatOpenAI`, and `donkey mock --scenario` parsing | Nothing; the LangChain act runs only if `[langgraph]` is installed |
| OpenAI script 03 | A `simulate()` loop over five refusal types, including `ContentSafetyBlocked` | Proxy credentials in the environment (no network calls) |

## Run it

```bash
make demo N=04
```

```bash
python "demos/human-made/openai/03 - typed-refusals-simulated.py"
```

## Key code

One context manager, and the branch runs; `times=` counts calls and normal
service resumes after (narrative demo 04):

```python
with donkey.simulate(PIIDetected):
    await agent.run("...")     # fails as a real PIIDetected

with donkey.simulate(TokenBudgetExceeded, times=2):
    await agent.run("a")   # refused
    await agent.run("b")   # refused
    await agent.run("c")   # succeeds — the injection is spent
```

Looping over refusal types on a plain OpenAI client (OpenAI script 03):

```python
async with Donkey.from_env() as donkey:
    client = donkey.openai()

    for refusal in REFUSALS:
        async with donkey.run(id=f"typed-refusals-{refusal.__name__}"):
            # simulate() replays the captured gateway fixture in-process, so
            # the refusal branch runs with no network and nothing to provoke.
            with donkey.simulate(refusal):
                try:
                    await client.responses.create(
                        model="gpt-4o",
                        input="Say hello in exactly three words.",
                    )
                except openai.APIStatusError as err:
                    report(classify(err.response))
```

Scripting the running simulator instead, for a stock client (narrative demo 04,
act 7):

```bash
donkey mock --scenario pii_block:every=2 \
            --scenario 'injection:on-pattern=ignore previous' \
            --scenario budget:limit=200,window=5s,cost=80
```

`pii_block` fails every Nth call, `injection` matches request text, and
`budget` is a real wall-clock window that serves the token-rate-limit 429 on
exhaustion until the window rolls over.

  `simulate()` only injects shapes that have a captured wire body.
  `simulate(ToolInvocationError)` and `simulate(GatewayUnavailable)` raise
  `ValueError` instead of inventing one — a transport failure has no HTTP
  response to replay. To provoke `GatewayUnavailable`, point at a dead origin
  (see [Typed refusals](https://donkey-development-kit.github.io/donkey-development-kit/examples/typed-refusals.md)).

Because the injection sits on the transport, it also reaches framework objects
the SDK does not wrap — demo 04 drives `donkey.langgraph.chat_model("gpt-4o")`
through `simulate(PIIDetected)` and gets the same taxonomy back.

**Learn more:** [Local simulator](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md) · [Testing & conformance](https://donkey-development-kit.github.io/donkey-development-kit/testing.md)

**Source:**
[narrative demo 04](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/claude-made/04_simulate_refusals) ·
[script 03](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/blob/main/demos/human-made/openai/03%20-%20typed-refusals-simulated.py)

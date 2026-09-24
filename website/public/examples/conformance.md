# Conformance suite

Four questions a team usually cannot answer about its own agent: does it retry
a budget refusal (it must not)? Does a typed refusal survive its error
handling? Does the run's correlation id reach its logs? Does it still work when
the gateway sends no budget headers? The conformance suite answers them without
reading your code — it swaps a fixture-serving transport underneath, calls
`agent.run(...)`, and watches the wire and the logs. So it grades behaviour, in
any framework, and it runs in your CI as a pytest plugin with no gateway.

| Example | Shows | Needs |
| --- | --- | --- |
| Narrative demo 05 | The suite failing a naive agent, what each finding means, the fixed agent passing, a correct exemption, and two broken exemptions failing at collection time | Nothing (`[test]` + `[llm]`) |

## Run it

```bash
make demo N=05
```

Against your own agent, the suite is a plain pytest invocation:

```bash
pip install "donkey-kit[test]"
pytest --donkey-conformance --agent=my_app.agent:build
```

## Key code

The agent under test is a factory the suite calls; it never reads the agent's
source. This is the fixed agent from `shipping_agent.py` — a refusal is
classified and escapes as its own type, it is never retried, and the
correlation id goes into the logs:

```python
class GovernedAgent:
    def __init__(self, donkey: Donkey) -> None:
        self._donkey = donkey
        self._client = donkey.openai()

    async def run(self, user_input: str) -> str:
        log.info(
            "handling request",
            extra={"correlation_id": current_correlation_id(), "input": user_input[:40]},
        )
        try:
            response = await self._client.responses.create(model="gpt-4o", input=user_input)
        except openai.APIStatusError as exc:
            error = classify(exc.response)
            log.warning(
                "governed refusal",
                extra={
                    "correlation_id": current_correlation_id(),
                    "refusal": type(error).__name__,
                },
            )
            raise error from exc
        return getattr(response, "output_text", "")

def build_governed(donkey: Donkey) -> GovernedAgent:
    return GovernedAgent(donkey)
```

When an agent genuinely cannot pass a scenario, you assert an exemption with a
reason (`exemptions.py`) and pass it on the command line:

```python
FRAMEWORK_LIMITS = {
    "correlation_id_propagated": (
        "This agent's framework owns the HTTP transport and offers no per-request "
        "context hook, so a run-scoped correlation id cannot reach the agent's logs."
    ),
}
```

```bash
pytest --donkey-conformance --agent=shipping_agent:build_naive \
       --donkey-known-limitations=exemptions:FRAMEWORK_LIMITS
```

  An exemption becomes an `EXEMPT` row with its reason attached — never a
  silent skip — and it excuses exactly the scenario it names. The mapping is
  validated at collection time, so a misspelled scenario name or an empty
  reason fails the run before any scenario executes.

The naive agent in the same file retries three times and re-raises a bare
`RuntimeError`. The suite flags the retried `TokenBudgetExceeded`, the lost
`PIIDetected` type, and the missing correlation id in its logs.

**Learn more:** [Testing & conformance](https://donkey-development-kit.github.io/donkey-development-kit/testing.md)

**Source:**
[narrative demo 05](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/claude-made/05_conformance)

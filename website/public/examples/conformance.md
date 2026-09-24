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

```text
════════════════════════════════════════════════════════════════════════════════════════
Demo 05 — the conformance suite
Four questions about your agent that you cannot currently answer.
════════════════════════════════════════════════════════════════════════════════════════

Run context
───────────
  target                 offline — no gateway, no simulator, no credentials
  output masking         on

[1] An agent written the way people actually write them

    for attempt in range(3):                     # retry on failure
        try:
            return await client.responses.create(...)
        except openai.APIStatusError as exc:
            if attempt == 2:
                raise RuntimeError(...) from exc  # friendly error

  Nothing there is obviously wrong. Retrying is a sane default, wrapping errors keeps
  stack traces out of the caller's face, and it logs what it is doing. Run the suite
  against it.

    pytest --donkey-conformance --agent=…:build_naive

    FFF.                                                                     [100%]
    Donkey conformance
    ==================
    FAIL  Retries a budget refusal        — retried a TokenBudgetExceeded 3× — a budget refusal is terminal; retrying only burns the same exhausted window
    FAIL  Swallows PII as a generic error — raised a bare RuntimeError — the PII refusal was swallowed as a generic error (bridge it with classify())
    FAIL  Propagates the correlation id   — did not emit the correlation id in any log record — read it from current_correlation_id() and include it when you log
    PASS  Works without budget headers    — completed a run when the gateway returned no budget headers
    Donkey conformance: 1 passed, 3 failed
    =========================== short test summary info ============================
    FAILED ::donkey-conformance::retries_token_budget - Retries a budget refusal:...
    FAILED ::donkey-conformance::swallows_pii_as_generic - Swallows PII as a gene...
    FAILED ::donkey-conformance::correlation_id_propagated - Propagates the corre...
    3 failed, 1 passed in 0.39s

  exit status            1

What each failure actually means
────────────────────────────────
  • Retried a TokenBudgetExceeded 3× — the window is already exhausted, so the retries
    cannot succeed and the extra calls make the rate-limit situation worse for everyone
    else on the same budget.
  • Raised a bare RuntimeError for a PII block — the caller wanted to catch PIIDetected
    and redact the flagged entities. It cannot, because the type was thrown away in the
    name of a friendlier message.
  • Never logged the correlation id — so when the platform team asks which gateway
    request corresponds to this run, there is no answer.

[2] The same agent, after the findings

    try:
        return await client.responses.create(...)
    except openai.APIStatusError as exc:
        error = classify(exc.response)
        log.warning("governed refusal", extra={
            "correlation_id": current_correlation_id(),
            "refusal": type(error).__name__,
        })
        raise error from exc          # typed, and not retried

    ....                                                                     [100%]
    Donkey conformance
    ==================
    PASS  Retries a budget refusal        — issued one call and did not retry the budget refusal
    PASS  Swallows PII as a generic error — surfaced the refusal as a typed PIIDetected
    PASS  Propagates the correlation id   — emitted the run's correlation id in its own logs
    PASS  Works without budget headers    — completed a run when the gateway returned no budget headers
    Donkey conformance: 4 passed, 0 failed
    4 passed in 0.36s

  exit status            0

[3] When an agent genuinely cannot pass, it says so out loud
  Suppose the correlation finding is not fixable: your framework owns the HTTP transport
  and gives you no per-request hook. That is a real limitation, so you assert it — with
  a reason — and it becomes an `exempt` row rather than a failure. It is never a silent
  skip, and the reason is meant to be published.

    # exemptions.py
    FRAMEWORK_LIMITS = {
        "correlation_id_propagated": "This agent's framework owns the HTTP …",
    }
    
    pytest --donkey-conformance --agent=shipping_agent:build_naive \
           --donkey-known-limitations=exemptions:FRAMEWORK_LIMITS

    FF..                                                                     [100%]
    Donkey conformance
    ==================
    FAIL    Retries a budget refusal        — retried a TokenBudgetExceeded 3× — a budget refusal is terminal; retrying only burns the same exhausted window
    FAIL    Swallows PII as a generic error — raised a bare RuntimeError — the PII refusal was swallowed as a generic error (bridge it with classify())
    EXEMPT  Propagates the correlation id   — This agent's framework owns the HTTP transport and offers no per-request context hook, so a run-scoped correlation id cannot reach the agent's logs.
    PASS    Works without budget headers    — completed a run when the gateway returned no budget headers
    Donkey conformance: 1 passed, 2 failed, 1 exempt
    =========================== short test summary info ============================
    FAILED ::donkey-conformance::retries_token_budget - Retries a budget refusal:...
    FAILED ::donkey-conformance::swallows_pii_as_generic - Swallows PII as a gene...
    2 failed, 2 passed in 0.35s

  exit status            1
  One row moved to EXEMPT with its reason attached. The other two findings are untouched
  — an exemption excuses exactly what it names.

[4] And an exemption you get wrong fails the run, loudly
  The mapping is validated at collection time, before any scenario runs. A typo'd
  scenario name would otherwise exempt nothing while looking like it exempted something,
  and an empty reason is a skip wearing a costume.

  case                   a misspelled scenario name
    ERROR: KNOWN_LIMITATIONS names unknown scenario 'retries_tokn_budget'; valid scenarios are ['correlation_id_propagated', 'retries_token_budget', 'swallows_pii_as_generic', 'works_without_budget_headers']
  exit status            4

  case                   an exemption with an empty reason
    ERROR: KNOWN_LIMITATIONS['retries_token_budget'] must be a non-empty reason string — an asserted exemption, never a silent skip
  exit status            4

The point
─────────
  This is the deliverable, not our internal adapter matrix. It ships as a pytest plugin
  so it runs in your CI, against your agent, in whatever framework you chose — and it
  needs no gateway to do it.

────────────────────────────────────────────────────────────────────────────────────────
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

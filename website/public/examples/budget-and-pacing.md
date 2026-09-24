# Budget & pacing

The gateway reports your token budget only in-band, on response headers —
there is no endpoint to ask how much is left. `donkey.budget` reads those
headers for you, so the window is an object rather than a header you parse.
The useful half is pacing: `pace(reserve=…)` refuses locally *before* issuing a
request that would cross your reserve, turning a 429 you would have to recover
from into an exception you chose to raise, and `wait_for_reset()` sleeps once
until the window rolls over.

| Example | Shows | Needs |
| --- | --- | --- |
| Narrative demo 03 | A cold process knowing nothing, the window updating per response, `pace(reserve=0.05)` raising `BudgetReserveReached`, `wait_for_reset()`, and the terminal 429 | Nothing (simulator) |
| OpenAI script 05 | `pace(reserve=)` letting the first call through, stopping the second, then `wait_for_reset()` | Proxy credentials |

## Run it

```bash
make demo N=03
```

```bash
python "demos/human-made/openai/05 - budget_and_pacing.py"   # needs proxy credentials
```

  On a live gateway the window arrives only when the token rate limit policy
  is applied to the proxy. The simulator synthesises a decreasing window so
  pacing can run locally; its happy-path numbers are illustrative, the parse
  path is not.

## Key code

Pacing, and recovering from it (narrative demo 03, act 3):

```python
try:
    async with donkey.budget.pace(reserve=0.05):
        await enrich(batch)
except BudgetReserveReached:
    await donkey.budget.wait_for_reset()   # one sleep, never a spin loop
```

Against a live proxy, the first call is unobserved so `pace()` lets it through
and the window arrives in-band; the second trips the reserve (OpenAI script 05):

```python
async with donkey.budget.pace(reserve=0.99999):
    response = await client.responses.create(
        model="gpt-4o",
        input="Say hello in exactly three words.",
    )
    budget = donkey.budget
    print("after request 1, budget remaining is", budget.remaining)
    print("after request 1, budget fraction_used is", budget.fraction_used)

try:
    async with donkey.budget.pace(reserve=0.99999):
        response = await client.responses.create(
            model="gpt-4o",
            input="Say hello in exactly three words.",
        )
except BudgetReserveReached as exc:
    print("stopped locally [in-script]", exc.fraction_used, exc.reserve)
```

An unobserved budget reports `None` for every field, never `0` — reporting `0`
remaining would stop an agent that could have run. `BudgetReserveReached` is
deliberately not a `PolicyViolation`: a refusal is the gateway saying no and is
terminal, while this is a local signal you are expected to recover from. If you
do cross the window, the resulting `TokenBudgetExceeded` is not retried by the
transport — retrying only burns the same window.

**Learn more:** [Budget & pacing](https://donkey-development-kit.github.io/donkey-development-kit/budget.md)

**Source:**
[narrative demo 03](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/claude-made/03_budget_and_pacing) ·
[script 05](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/blob/main/demos/human-made/openai/05%20-%20budget_and_pacing.py)

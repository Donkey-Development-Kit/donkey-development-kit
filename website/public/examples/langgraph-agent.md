# LangGraph agent

A real multi-step agent: the model decides to call two tools, the tools
return, and the model composes an answer. Every model call in that loop goes
through the governed proxy, and the object driving it is LangChain's own
`ChatOpenAI`, not a wrapper. The only DDK lines are the one that builds the
model, `donkey.run(id=…)` around the loop, `typed_refusals()` so a proxy 403
comes out of `astream` as `PIIDetected` rather than a framework-wrapped error,
and `@donkey.tool` on the two functions. Governance sits at the boundary, not
in the agent's control flow.

| Example | Shows | Needs |
| --- | --- | --- |
| Narrative demo 09 | `donkey.langgraph.chat_model()`, a `create_agent` loop calling two tools, then the run's budget, `last_call` and the registered tools | Live credentials + `[langgraph]` |

## Run it

```bash
make demo N=09          # needs live credentials
```

  This example needs a live gateway. The local simulator replays a captured
  `/responses` completion and will not decide to call tools, so there is no
  offline version. Without credentials it exits cleanly with setup guidance.
  The refusal path *can* run offline: [Simulating refusals](https://donkey-development-kit.github.io/donkey-development-kit/examples/simulating-refusals.md)
  drives the same `ChatOpenAI` through `donkey.simulate()`.

## Key code

The tools are plain LangChain tools, marked for the SDK's registry:

```python
@tool
@Donkey.tool
def check_inventory(sku: str) -> str:
    """Return the units in stock and warehouse for a product SKU."""
    return INVENTORY.get(sku, "unknown SKU")

@tool
@Donkey.tool
def get_price(sku: str) -> str:
    """Return the list price for a product SKU."""
    return PRICES.get(sku, "unknown SKU")
```

The model and the governed loop:

```python
async with Donkey.from_env() as donkey:
    model = donkey.langgraph.chat_model(MODEL, temperature=0)
    agent = create_agent(model, tools=[check_inventory, get_price])

    async with donkey.run(id="sku-lookup"):
        with donkey.langgraph.typed_refusals():
            async for chunk in agent.astream(
                {"messages": [("user", QUESTION)]}, stream_mode="updates"
            ):
                ...

    budget = donkey.budget
    last = donkey.last_call
```

After the loop, `donkey.budget` reflects the run's real consumption across
every model call, and `donkey.last_call` describes the most recent one — who
served it, what they served and what it cost. The adapter targets the
`/responses` route (`use_responses_api=True`), the same one `donkey.openai()`
uses. `DEMO_MODEL` defaults to `gpt-4o-mini` in this example; set it to a model
your proxy routes.

If the gateway is unavailable, [Framework objects](https://donkey-development-kit.github.io/donkey-development-kit/examples/framework-objects.md)
constructs the same real framework objects with no network.

**Learn more:** [LangGraph](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/langgraph.md) · [Model access](https://donkey-development-kit.github.io/donkey-development-kit/frameworks.md)

**Source:**
[narrative demo 09](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/claude-made/09_langgraph_agent)

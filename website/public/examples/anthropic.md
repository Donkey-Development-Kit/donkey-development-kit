# Anthropic

The native `anthropic` client from `donkey.anthropic.client()`, on the
governed transport. It sends `/v1/messages`, so it needs a proxy provisioned
**`Format=Anthropic`** (for example `ddk-anthropic-inbound`). The default DDK
proxies are `Format=OpenAI` and return 404 on `/v1/messages`. Because the
client shares the governed transport, `last_call` and `simulate()` work just
as they do for OpenAI.

| # | Script | Shows | Needs |
| --- | --- | --- | --- |
| 01 | `native-messages.py` | A native Messages call and `last_call` | Proxy credentials, `Format=Anthropic` |
| 02 | `typed-refusals-simulated.py` | Four simulated refusals, typed | Nothing — any placeholder values |

## Install

Follow the [examples setup](https://donkey-development-kit.github.io/donkey-development-kit/examples.md#setup) first, then:

```bash
python -m pip install -e "../donkey-development-kit/python[llm,anthropic]" "anthropic<1"
set -a; source .env.local; set +a
export DONKEY_LLM_PROXY_URL=https://<host>/ddk-anthropic-inbound/   # 01 only; overrides the file
```

  **Pin `anthropic<1`.** The `anthropic` 1.x releases (September 2026) moved
  to `httpx2` and reject the SDK's `httpx`-based governed client with
  `TypeError: Invalid http_client argument`. These examples were run with
  `anthropic` 0.125.0.

The client id and secret are the same pair as the other proxies. The model is
`claude-haiku-4-5-20251001` (the `MODEL` constant).

## 01 — Native Messages

```bash
python "demos/human-made/anthropic/01 - native-messages.py"
```

```python
async with Donkey.from_env() as donkey:
    client = donkey.anthropic.client()   # native AsyncAnthropic, POST /v1/messages

    raw = await client.messages.with_raw_response.create(
        model=MODEL,
        max_tokens=32,
        messages=[{"role": "user", "content": "Say hello in exactly three words."}],
    )
    print(raw.parse().content[0].text)

    last = donkey.last_call
    print("served_provider", last.served_provider)
    print("served_model   ", last.served_model)
    print("input_tokens   ", last.input_tokens)
    print("output_tokens  ", last.output_tokens)
    print("request-id     ", raw.headers.get("request-id"))
    print("request_id     ", last.request_id)
```

**You should see:** the reply; `served_provider`, `served_model`, and input and
output tokens from `last_call`; then Anthropic's `request-id` header next to
`last_call.request_id`. `request-id` is not one of the headers the SDK reads
for `request_id` yet, so the two can differ, or the field can be `None`.

## 02 — Typed refusals, simulated

```bash
python "demos/human-made/anthropic/02 - typed-refusals-simulated.py"
```

`donkey.simulate(...)` works for the Anthropic client too, because it shares
the governed transport. Each refusal surfaces as the matching Anthropic error
class, and `classify()` maps its response to the SDK type.

```python
client = donkey.anthropic.client()
for refusal in REFUSALS:
    async with donkey.run(id=f"anthropic-simulated-{refusal.__name__}"):
        with donkey.simulate(refusal):
            try:
                await client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=32,
                                             messages=[{"role": "user", "content": "hello"}])
            except anthropic.APIStatusError as err:
                error = classify(err.response)
                print(type(err).__name__, "->", type(error).__name__, error.policy, error.correlation_id)
```

```text
PermissionDeniedError -> PIIDetected pii-detection anthropic-simulated-PIIDetected
BadRequestError -> PromptInjectionBlocked prompt-injection-protection anthropic-simulated-PromptInjectionBlocked
PermissionDeniedError -> ContentSafetyBlocked content-safety anthropic-simulated-ContentSafetyBlocked
RateLimitError -> TokenBudgetExceeded token-rate-limit anthropic-simulated-TokenBudgetExceeded
```

The left column is what Anthropic's client alone would tell you — a 403 is a
`PermissionDeniedError` whether it was PII or content safety. The right column
is what the gateway actually decided.

**Learn more:** [Anthropic SDK](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/anthropic.md)

**Source:**
[`demos/human-made/anthropic/`](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/human-made/anthropic)

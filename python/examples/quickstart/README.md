# Gateway-free quickstart

From `pip install` to a typed refusal caught in code — **no Anypoint
credentials, no real gateway** (#203, BG §1.4).

**What this shows.** `main.py` boots the local gateway simulator (BG §1.4) in
an in-process daemon thread, points the SDK at it, and makes two governed calls
through `donkey.llm.client(sync=True)` — a real `openai.OpenAI` wired to the
Donkey transport. That one transport is what gives you the three things a bare
`base_url` + headers cannot:

1. **A span.** Each call auto-emits a `donkey.llm.chat` GenAI span. The script
   wires a `ConsoleSpanExporter`, so you see the span — `gen_ai.*` usage tokens,
   `donkey.policy.decision`, `donkey.budget.remaining`, the correlation id —
   printed to your terminal.
2. **A budget object.** `donkey.budget` is updated from the response's rate-limit
   header; the script prints `donkey.budget.remaining`.
3. **A typed refusal.** A second call with `model="donkey-sim/pii-detected"`
   makes the simulator replay a captured PII-rejection body byte-for-byte;
   `classify()` turns the raw response into a typed `PIIDetected` you branch on.

The simulator **ignores auth** and stamps every response with
`x-donkey-simulator: true` — it is a fixture replay, never a real gateway. The
`client_id` / `client_secret` the script sets are throwaway placeholders, never
real credentials.

> 📖 **Prefer reading to running?** The canonical walkthrough is the docs
> quickstart:
> **[Quickstart](https://donkey-development-kit.github.io/donkey-development-kit/quickstart)**.
> This example is the exact code that page documents, kept in lockstep; it is
> executed and timed in CI (the `quickstart` job) so it can never silently rot.

## Run

```bash
pip install "donkey-kit[llm,local,otel]"

python examples/quickstart/main.py
```

No environment variables to set — the script boots the simulator and points
`DONKEY_LLM_PROXY_URL` at it for you. It exits `0` on success.

## When you're ready: point at your real gateway

Drop the simulator and set the three governed-access values, and the *same code*
runs against your Omni Gateway proxy (the `client_id` / `client_secret` header
pair is consumer auth — not a bearer token, and separate from any Anypoint
control-plane credential):

```bash
export DONKEY_LLM_PROXY_URL="https://<ingress-gw>/<instance>/"   # note: no /v1
export DONKEY_LLM_PROXY_CLIENT_ID="<consumer client id>"
export DONKEY_LLM_PROXY_CLIENT_SECRET="<consumer client secret>"
```

## Links

- Quickstart docs: https://donkey-development-kit.github.io/donkey-development-kit/quickstart
- Error taxonomy: https://donkey-development-kit.github.io/donkey-development-kit/errors

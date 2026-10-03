# Governed error taxonomy

Live

The proxy doesn't just pass model calls through — it enforces policy. When it
rejects a call, DDK turns the response into a **typed exception** so you
branch on the governance outcome instead of parsing bodies.

## The rejection shapes `classify()` types

`classify()` types nine rejection shapes. **Neither the status code nor the
shape of the `error` value alone is a sufficient discriminator** — a `403` can be
PII, a killed agent, a regex-guard block, a content-safety block (all policy
blocks) *or* auth,
and the same nested-object envelope is emitted by both the upstream provider and
a gateway policy. The authoritative discriminator is the error **`type`** plus
specific headers.

| Rejection | HTTP | Discriminator | Maps to |
|---|---|---|---|
| Client-ID enforcement (auth) | `401` | flat `{"error":"…"}` + `www-authenticate: Client-ID-Enforcement` | `AuthError` |
| PII detected | `403` | nested `{"error":{type:"pii_detected"}}`, **no** `www-authenticate` | `PIIDetected` (parses `entities`) |
| Agent kill switch | `403` | nested `{"error":{code:"agent_killed"}}`, **no** `type`, **no** `www-authenticate` | `AgentKilled` |
| Injection protection | `400` | header `x-injection-protection: blocked` (**not** the status) | `PromptInjectionBlocked` |
| Regex prompt guard | `403` | top-level `matched_patterns` list (flat `error`) | `PromptInjectionBlocked` (`policy="regex-prompt-guard"`) |
| Content safety / guardrails | `403` | header `x-llm-proxy-<vendor>-…-action: reject` (Azure Content Safety / Bedrock Guardrails) | `ContentSafetyBlocked` (parses `categories`) |
| Token rate limit | `429` | **empty body**; `x-token-limit`/`-remaining`/`-reset` headers (ms) | `TokenBudgetExceeded` (`retry_after` derived) |
| Content moderation (undiscriminated) | `4xx` | falls through — no nested `error`, no injection/guard/safety discriminator | generic `PolicyViolation` |
| Upstream provider 4xx | `4xx` | nested `error` object **with** `code`/`type`/`param` — in an OpenAI-style object envelope `{"error":{…}}` **or** a Gemini-style list envelope `[{"error":{…}}]` (`status`→`error_type`) | `UpstreamRequestError` |
| Upstream 5xx | `5xx` | status range (no competing discriminator) | `UpstreamModelError` (retryable) |
| Bare model name on a multi-provider proxy | `400` | flat `{"error":"…"}` saying the model "is not in the known unique model map" | `ModelNotRoutable` (`.model`) |

`PIIDetected`, `AgentKilled`, the regex-prompt-guard check, and the content-safety
check are all evaluated **before** the generic 401/403→auth rule, because each is
a `403` (or `4xx`) that is *not* an auth failure. `AgentKilled` is also checked
before the upstream-4xx rule: its nested `error` object would otherwise read as
an upstream request mistake, when the upstream was never called. Likewise the injection check gates on the
`x-injection-protection` header, so an ordinary malformed `400` stays an
ordinary refusal.

Client-ID enforcement (`401`) is a **consumer-auth** case, not one of the nine
policy-rejection rows.

The Injection Protection shape is live-verified against a deployed proxy
(#669); `classify()` keys on the header discriminator alone for it, not the
body.

## The exception tree

All importable from `donkey_kit`, along with `classify`:

```
DonkeyError                     # base of the whole tree
├─ ConfigError                  # misconfiguration or client misuse (raised locally, no request sent)
├─ AuthError                    # rejected data-plane credentials or control-plane auth
├─ PolicyViolation              # base for every governance rejection
│  ├─ PIIDetected               # 403, type=pii_detected; .entities, .gateway_message
│  ├─ AgentKilled               # 403, code=agent_killed — the Agent Kill Switch blocked this agent
│  ├─ TokenBudgetExceeded       # 429; .retry_after (seconds)
│  ├─ PromptInjectionBlocked    # x-injection-protection: blocked, or regex matched_patterns
│  └─ ContentSafetyBlocked      # Azure Content Safety / Bedrock Guardrails vendor reject header; .categories
├─ GatewayUnavailable           # transport failure — gateway unreachable, NO response; .base_url/.cause (ungoverned)
├─ UpstreamRequestError         # upstream 4xx; .code/.error_type/.param
├─ ModelNotRoutable             # 400, bare model name on a multi-provider proxy; .model
├─ UpstreamModelError           # upstream 5xx — provider error, retryable
├─ BudgetReserveReached         # client-side, from budget.pace(); .fraction_used/.reserve/.reset_at
├─ ModelSubstituted             # client-side, opt-in; .requested_model/.served_model/.served_provider
├─ PlatformTeamOnly             # Governance.apply() without the platform-team opt-in; also a PermissionError
└─ ToolInvocationError, RegistryError, PublicationDrift, ProvisioningError, GovernanceDrift
                                # tool access, registry, publishing and provisioning (Roadmap surfaces)
```

`GatewayUnavailable` is deliberately **not** under `PolicyViolation`: it is the one
*ungoverned* failure in the tree (see below). Everything under `PolicyViolation` is
something the gateway told the SDK; `GatewayUnavailable` is the gateway not being
there to tell it anything.

## Cookbook: every exception — discriminator, retryable, next step

One row per exception you can catch, with the three facts you need to write a
handler: **what tells it apart** (the discriminator), **whether retrying it can
ever succeed**, and **the next step** its `.remediation` names. "Retryable" here
means *by you* — the transport already retries the only class that is safe to
(`UpstreamModelError`), and treats every governance refusal as terminal so it can
never burn an exhausted budget or replay a blocked prompt.

| Exception | Discriminator | Retryable? | Next step (`.remediation`) |
|---|---|---|---|
| `AuthError` | Data plane: `401`, or `403` + `www-authenticate`. Control plane: Anypoint auth-provider or connected-app token acquisition fails. | **No** — terminal. In `jwt` mode the async data-plane client refreshes the wallet JWT and retries **once** on a `401`, then surfaces it; in client-id mode a data-plane `401` surfaces immediately. Control-plane token acquisition surfaces immediately and never affects model calls. | Data plane: check the consumer `client_id` / `client_secret` pair and API Manager authorization. Control plane: check the configured auth provider; for a connected app, verify `ANYPOINT_CLIENT_ID` / `ANYPOINT_CLIENT_SECRET` and the required scopes. |
| `PIIDetected` | `403`, nested `type: "pii_detected"`, **no** `www-authenticate` | **No** — a `PolicyViolation`, never retried. | Remove or redact the flagged values (`.entities`), or relax the policy's entity list in API Manager. |
| `AgentKilled` | `403`, nested `code: "agent_killed"`, **no** `type` | **No** — a `PolicyViolation`, never retried; the agent stays blocked until an administrator restores it. | Ask an administrator to restore this agent's model access in Governance > Security. |
| `TokenBudgetExceeded` | `429`, empty body, `x-token-*` headers | **Not immediately** — never auto-retried; only worth retrying *after* the window resets. | Wait for `.retry_after` (seconds) / the reset, then retry — or request an increase in API Manager. |
| `PromptInjectionBlocked` | header `x-injection-protection: blocked`, **or** a top-level `matched_patterns` list (regex prompt guard) | **No** — a `PolicyViolation`, never retried. | Review and sanitise the untrusted input, or adjust the policy's sensitivity / deny-list in API Manager. |
| `ContentSafetyBlocked` | `403` + `x-llm-proxy-<vendor>-…-action: reject` (Azure Content Safety / Bedrock Guardrails) | **No** — a `PolicyViolation`, never retried. | Revise the flagged content (`.categories`), or adjust the policy's categories / severity thresholds in API Manager. |
| `PolicyViolation` (generic) | a `4xx` matching **no** known rejection shape | **No** — terminal. | Inspect `.response`; file an issue with the status/headers/body so the shape can be typed. |
| `UpstreamRequestError` | non-`429` `4xx`, nested `error` with `code`/`type`/`param` (object **or** Gemini list envelope) | **No** — a client-side request mistake passed through the gateway, terminal. | Fix the flagged model or parameter (`.code` / `.param`); if `model_not_found`, request the model in API Manager. |
| `ModelNotRoutable` | `400`, flat `error` saying the model "is not in the known unique model map" (model-based routing with more than one provider) | **No** — a client configuration mistake, terminal; the upstream was never called. | Use the `provider/model` form, e.g. `openai/gpt-5-mini` instead of `gpt-5-mini`. |
| `UpstreamModelError` | `5xx` | **Yes** — the transport already retries `502` / `503` / `504`; a persistent `5xx` is safe for you to retry too. | Transient provider failure — retry, then escalate if it persists. |
| `GatewayUnavailable` | transport failure — DNS, refused connection, TLS, timeout — with **no** HTTP response | **Not automatically** — terminal here; you may retry or fall back. | Check host reachability, `.base_url`, and network egress; run [`donkey doctor`](https://docs.donkey-kit.dev/cli.md). |

Two more `DonkeyError`s are **client-side signals**, not gateway refusals, so
they sit outside the retry question. `BudgetReserveReached` is raised *before* a
call by [`donkey.budget.pace()`](https://docs.donkey-kit.dev/budget.md) and is meant to be recovered from
(`await donkey.budget.wait_for_reset()`, then continue) when its `.reset_at` is
known. If `.reset_at` is `None`, propagate or handle it instead — waiting returns
immediately and an unconditional retry would spin. Its `.remediation` carries
that branch as an inspectable next step, so you don't have to parse the
exception message. `ModelSubstituted` reports that a call *succeeded* against a
different model than requested (opt-in via `on_model_substitution="raise"`).
`ConfigError` is raised locally, before any request. It reports every missing
field at once, and it also reports an endpoint that may not receive the
configured credentials: a non-`https://` URL, or a URL from the project's config
files paired with credentials from elsewhere. See
[Which credentials a URL receives](https://docs.donkey-kit.dev/reference/configuration.md#which-credentials-a-url-receives).
Fix the config and re-run. The transport raises `ConfigError` for two lifecycle
mistakes as well, so neither escapes as a bare `RuntimeError`: a call on a
`Donkey` whose HTTP client is closed (after `aclose()`, or after a framework
closed the client it was given), and a call whose pooled connections belong to
an event loop that has closed. The SDK's own connection pools are per event
loop, so a second `asyncio.run()` on one `Donkey` works; this one comes from a
transport you passed in and reused across `asyncio.run()` calls. Its
`.remediation` names the fix for each. Through the OpenAI SDK it arrives
wrapped, like `GatewayUnavailable`: catch `openai.APIConnectionError` and read
the `ConfigError` from `e.__cause__`.

`AuthError.remediation` follows the plane that failed. Errors classified from
an LLM-proxy response use the canonical consumer-credential guidance that
[`donkey doctor`](https://docs.donkey-kit.dev/cli.md) also prints. Control-plane token failures override that
default with guidance for the provider that failed: connected-app errors point
to the Anypoint credentials and scopes, while an exhausted `ChainedAuth` points
to each configured provider's credential or token source.

## When the gateway can't be reached at all

Every rejection above describes something the gateway *told* the SDK.
`GatewayUnavailable` is the opposite: a transport-level failure — DNS, refused
connection, TLS error or timeout — with **no HTTP response** behind it. It is the
one *ungoverned* failure the taxonomy names, so a long-running agent can tell
"lost the gateway" apart from any other network fault and react — checkpoint,
queue, shed load, or fall back to a non-AI path — instead of pattern-matching a
raw `httpx` exception.

`DonkeyAsyncClient` and its blocking twin both raise it, so the async and sync
surfaces behave identically. An HTTP SDK on top re-wraps it (the OpenAI SDK as
an `APIConnectionError`); inside `donkey.run()` it reaches you as
`GatewayUnavailable` again (see
[Typed refusals at the framework boundary](#typed-refusals-at-the-framework-boundary)).
It is terminal and **not retried**. It carries:

- `.base_url` — the origin that failed, on the exception, not only in the message.
- `.cause` — the underlying `httpx` exception (also chained via `raise … from`).
- `.request_id` — always `None`; there was no response to read the upstream provider's id from.
- `.correlation_id` / `.call_id` — the run and per-call ids the client sent, carried
  even though no response came back, so the failure joins your logs like any other.

Its `.remediation` names the three real causes — an unreachable host, a wrong
base URL, or blocked network egress — and points at `donkey doctor` for
connectivity diagnosis.

## Every refusal names a next step

Every `DonkeyError` — not just every refusal — carries a non-empty,
human-readable `remediation`, so `except DonkeyError as e: log(e.remediation)`
is always safe. The constructor **raises** if you try to build one with a blank
remediation, every class accepts a `remediation=` override, and each class ships
a canonical default. For the refusals:

- `PIIDetected` → remove or redact the flagged values, or relax the policy's
  entity list in API Manager.
- `AgentKilled` → ask an administrator to restore this agent's model access in
  Governance > Security.
- `TokenBudgetExceeded` → wait for the window to reset (see `retry_after`) or
  request an increase.
- `PromptInjectionBlocked` → review and sanitise the untrusted input, or adjust
  the policy's sensitivity.
- `ContentSafetyBlocked` → revise the flagged content, or adjust the policy's
  categories / severity thresholds.

The text names the **action you can take**, not the policy that fired. Because
each default lives on the exception class, it is a single source of wording that
[`donkey doctor`](https://docs.donkey-kit.dev/cli.md) reuses for its own failure output, so the CLI and the
exception never disagree.

## Refusal messages don't repeat blocked content

The PII policy's rejection text repeats every value it flagged. `PIIDetected`
builds its own message instead, from the entity types, their count and their
character offsets. So `str(exc)`, `repr(exc)` and `exc.args` never contain the
blocked value, and neither does a log line, a traceback or
[`donkey doctor`](https://docs.donkey-kit.dev/cli.md):

```
Request blocked: personally identifiable information detected (403): 1 entity (Email at chars 12-32). Values withheld; the gateway's text is on .gateway_message.
```

`.entities` lists the flagged types (`["Email"]`). The gateway's own text is on
`.gateway_message`, and the raw body is on `.response`. Neither is rendered by
`str()` or `repr()`. Both carry the blocked content, so handle them like the
prompt itself. The other refusal messages are built from status codes,
headers, category names and policy pattern names, never from the request.

A traceback also prints every chained exception, and a framework's own error
usually repeats the gateway's text. So when the SDK maps a framework error to
a typed one, as `donkey.run()` and
[`typed_refusals()`](#typed-refusals-at-the-framework-boundary) do, it raises
it without a chained cause: `exc.__cause__` is `None`, and the
framework error is on `exc.framework_error` (`None` when there was none). No
frame in the traceback holds the framework error as a local variable, so error
reporters that print frame locals (Sentry, `pytest -l`) don't show it either.
Python still keeps it on the suppressed `exc.__context__`, so treat
`framework_error` and `__context__` like `gateway_message`.

This applies to policy refusals. `UpstreamRequestError` messages include the
upstream provider's own error text, which can quote parts of your request. For
everything the SDK does and doesn't hide in printed output, see
[What printed output hides](https://docs.donkey-kit.dev/reference/configuration.md#what-printed-output-hides).

## The ids every `DonkeyError` carries

Every exception in the tree carries three ids so you can join a failure to your
logs and to the gateway's own record:

| Attribute | What it is | Provenance |
| --- | --- | --- |
| `.correlation_id` | The **run** id, shared by every call in a [`donkey.run()`](https://docs.donkey-kit.dev/telemetry.md#correlation-ids) block | The `X-Correlation-Id` request header the client sent — always equals what went on the wire. |
| `.call_id` | The **per-call** id, unique per logical request and stable across that request's retries | The `X-Donkey-Request-Id` request header the client sent. Present **even when the request fails before any response** (a transport error). |
| `.request_id` | The **upstream provider's own** id, passed through by the gateway | Read back from a **response** header whose name varies by provider (`x-request-id` for OpenAI, `x-amzn-requestid` for Bedrock, `apim-request-id` for Azure, `request-id` for a native Anthropic proxy). Quote it to the provider's support team. Absent on a transport error, or on a route where the provider forwarded none. |

`classify(response)` fills `.correlation_id` and `.call_id` from the response's
own request, so a refusal typed at the framework boundary (below) needs no extra wiring — the
correlation id on the exception equals the header that was actually sent. (If you
overrode the header names in config, pass the ids to `classify()` explicitly.)

## Typed refusals at the framework boundary

Every framework between your code and the gateway raises its own errors. The
OpenAI and Anthropic SDKs turn a refusal into a `PermissionDeniedError` and a
lost gateway into an `APIConnectionError` (the SDK's `GatewayUnavailable` hidden
on its `__cause__`), LangChain re-wraps those again, and Strands and Agent
Framework wrap them in their own types. Inside a
[`donkey.run()`](https://docs.donkey-kit.dev/telemetry.md#correlation-ids) block or a
[`@donkey.governed`](https://docs.donkey-kit.dev/telemetry.md#correlation-ids) function, none of that reaches
you: a refusal or a lost gateway leaves the block as its typed `DonkeyError`,
so one `except` covers every framework.

```python
from donkey_kit import GatewayUnavailable, PIIDetected, TokenBudgetExceeded

client = donkey.openai()

try:
    async with donkey.run(id=ticket.id):
        resp = await client.chat.completions.create(model="gpt-4o", messages=msgs)
except PIIDetected as e:
    print("blocked, entities:", e.entities)
except TokenBudgetExceeded as e:
    print("slow down; retry after", e.retry_after, "s")
except GatewayUnavailable as e:
    print("could not reach the proxy:", e.base_url)
```

The same block around a LangGraph `graph.ainvoke(...)`, an Anthropic
`messages.create(...)` or a Strands agent raises the same classes. The
blocking forms behave the same: `with donkey.run():` and a sync
`@donkey.governed` function.

Outside a run, `typed_refusals()` is the same bridge on its own. It works as a
sync or async context manager and as a decorator for sync and async functions,
and every adapter exposes it as `donkey.<framework>.typed_refusals()`:

```python
from donkey_kit import typed_refusals

with typed_refusals():
    reply = client.chat.completions.create(model="gpt-4o", messages=msgs)

@typed_refusals()
async def answer(question: str) -> str: ...
```

What the bridge types, and what it leaves alone:

- **A typed error the SDK raised**, such as `GatewayUnavailable` or
  `ModelSubstituted`, is found on the framework error's cause chain and
  re-raised as it is.
- **A gateway rejection** is classified from the response the framework
  error carries, with the correlation and call ids that were sent, exactly as
  `classify()` would. Only a response the SDK's own transport sent is
  classified. A `403` from some other HTTP call in the block is not a governed
  refusal and passes through.
- **Anything else** propagates unchanged: your own bugs, your own
  `raise HTTPException(...) from exc`, a `KeyboardInterrupt`, a task
  cancellation.

The framework's own error stays on `exc.framework_error`. To get the
framework's errors instead, pass `typed_refusals=False` to `donkey.run()` or
`@donkey.governed`.

  The bridge only sees calls that went through the SDK's transport. ADK's
  `model()` (LiteLLM) and CrewAI own their transport, so their errors pass
  through untyped. These are the same
  [conformance exemptions](https://docs.donkey-kit.dev/testing.md#exemptions) as their correlation ids.

### Classifying a response yourself

`classify()` is the building block underneath. Apply it to any
`openai.APIStatusError` (or Anthropic status error) you caught yourself:

```python
import openai
from donkey_kit import PIIDetected, classify

try:
    resp = await client.chat.completions.create(model="gpt-4o", messages=msgs)
except openai.APIStatusError as e:
    governed = classify(e.response)          # -> a DonkeyError subclass
    if isinstance(governed, PIIDetected):
        print("blocked, entities:", governed.entities)
```

The blocking client from `donkey.llm.client(sync=True)` behaves identically here.
Drop the `await`: it is the same OpenAI SDK raising the same
`openai.APIStatusError`, and `classify()` reads the response the same way.

## Retry behaviour

Both clients retry only transient upstream/gateway failures (502/503/504) and
treat every 4xx as terminal — **including a 429**: on this proxy a 429 is a
token-budget refusal (`TokenBudgetExceeded`), so retrying it would only burn the
same already-exhausted window. `retry_after` is still surfaced for you to pace
against, but the transport never silently retries it.

In `jwt` mode the async client additionally refreshes the wallet JWT and
retries **once** on a 401. In the default client-id mode the data-plane client
holds no token, so a 401 is terminal, as it is on the blocking client, and
surfaces immediately as `AuthError`. The Anypoint control-plane credential
lives on a separate client: model calls never fetch, send or refresh it. See
[What the SDK sends where](https://docs.donkey-kit.dev/reference/configuration.md#what-the-sdk-sends-where).

## Unrecognised shapes

Any content-moderation or federated-guardrail response that matches none of the
discriminators above falls through to a generic `PolicyViolation` rather than an
invented type. DDK only types a refusal by a discriminator it can identify
reliably; everything else stays inspectable via `.response`.

One `400` that used to land here is now typed: a bare model name (`gpt-5-mini`)
sent to a model-based proxy with more than one provider. The gateway rejects it
before any upstream call, and `classify()` returns `ModelNotRoutable`. It keeps
the gateway's text and is not a `PolicyViolation`.

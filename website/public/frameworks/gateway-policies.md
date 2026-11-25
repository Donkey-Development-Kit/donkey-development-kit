# Model gateway policies

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

The policies applied to an LLM proxy instance in API Manager decide what
happens to every model call: who may call, how many tokens and requests they
may spend, which prompts are refused, and which provider serves the request.
The gateway enforces them. DDK reimplements none of them. It reads what the
gateway answered and turns each outcome it can identify into a typed exception
or a field your code can branch on.

  **Three things are called "policy" in these docs.** *Gateway policies* are
  what the gateway enforces on each surface: this page for models, and the
  [MCP tools](https://docs.donkey-kit.dev/tool-access/gateway-policies.md) and
  [A2A agents](https://docs.donkey-kit.dev/a2a/gateway-policies.md) pages. *[Custom policies](https://docs.donkey-kit.dev/custom-policies.md)*
  teach `classify()` the refusal shape your own gateway policy returns. The
  *[policy handshake](https://docs.donkey-kit.dev/policies.md)* is a planned, advisory read of the policies in
  force; the gateway always wins.

Each section carries the status of the ledger row it rests on. Live
means the shape was captured from a real gateway.
The Policy-source
badge means the shape was read from the policy's source and has not been
captured live yet. The page's own status is the weakest one on it.

This page says what each policy means for your code. The exact discriminators
(status, headers, body) are in
[the rejection shapes `classify()` types](https://docs.donkey-kit.dev/errors.md#the-rejection-shapes-classify-types),
and the retry rule and next step for every exception are in the
[cookbook](https://docs.donkey-kit.dev/errors.md#cookbook-every-exception--discriminator-retryable-next-step).

## At a glance

| Policy | What the gateway does | What your code sees | Rehearse it offline |
| --- | --- | --- | --- |
| [Client ID enforcement](#client-id-enforcement) | Rejects a call without a valid consumer credential | `AuthError` | `donkey.simulate(AuthError)` |
| [Token rate limiting](#token-rate-limiting) | Caps tokens per window | `TokenBudgetExceeded`; `donkey.budget` | `--scenario budget:…` |
| [Request rate limiting](#request-rate-limiting) | Caps requests per window | `RequestRateLimitExceeded`; `donkey.budget.requests` | `--scenario request_limit:…` |
| [Model wallet](#model-wallet) | Authenticates with a JWT and spends from a wallet budget | `TokenBudgetExceeded` when the wallet is exhausted | `donkey.simulate(TokenBudgetExceeded)` |
| [PII detection](#pii-detection) | Refuses a prompt carrying personal data | `PIIDetected` | `--scenario pii_block:…` |
| [Injection Protection](#injection-protection-and-regex-prompt-guard) | Refuses a prompt-injection attempt | `PromptInjectionBlocked` | `--scenario injection:…` |
| [Regex Prompt Guard](#injection-protection-and-regex-prompt-guard) | Refuses a prompt matching configured patterns | `PromptInjectionBlocked` | `donkey.simulate(PromptInjectionBlocked)` |
| [Content safety and guardrails](#content-safety-and-guardrails) | Refuses content an external moderation service flags | `ContentSafetyBlocked` | `donkey.simulate(ContentSafetyBlocked)` |
| [CrowdStrike AIDR Guardrails](#crowdstrike-aidr-guardrails) | Same, through CrowdStrike AIDR | `ContentSafetyBlocked` | `donkey.simulate(ContentSafetyBlocked)` |
| [Agent Kill Switch](#agent-kill-switch) | Blocks a killed agent outright | `AgentKilled` | `donkey.simulate(AgentKilled)` |
| [OAuth2 OBO step-up (CIBA)](#oauth2-obo-step-up-ciba) | Holds the call until the user approves it | `GatewayApprovalPending` / `GatewayApprovalDenied` | `--scenario ciba:…` |
| [Model-based routing](#model-based-routing-and-fallback) | Picks the provider from the model name, with fallback | `ModelNotRoutable`; `last_call` routing fields | `donkey.simulate(ModelNotRoutable)` |
| [Semantic routing](#semantic-routing) | Picks the provider from the prompt's topic | `last_call.matched_topic`, `.routing_score` | — |
| [Semantic caching](#semantic-caching) | Answers from a stored completion | `last_call.cache_status`; `donkey.cache()` steering | — |
| [Anything else](#unrecognised-refusals) | A refusal with no known discriminator | `PolicyViolation` | `donkey.simulate(PolicyViolation)` |

`donkey.simulate(...)` refuses the next calls in-process with the captured
fixture ([Testing](https://docs.donkey-kit.dev/testing.md#simulate--in-process-no-server)). The
`--scenario` rules script `donkey mock` across a whole run
([Local simulator](https://docs.donkey-kit.dev/simulator.md#scenario-scripting)).

## Client ID enforcement

Live

The [Client ID Enforcement](https://docs.mulesoft.com/gateway/latest/policies-included-client-id-enforcement)
policy rejects a call whose `client_id` / `client_secret` pair is missing or not
registered for the instance. The gateway answers `401` with
`www-authenticate: Client-ID-Enforcement`, and DDK raises `AuthError`.

`AuthError` is never confused with a policy block. The PII, kill-switch,
guardrail and CIBA checks all run before the generic `401`/`403` rule, so a
refusal of the *content* never tells you to rotate a credential. Fix the
credential, or request access to the instance. In
[JWT mode](#model-wallet) the async client refreshes the token and retries once
on a `401` before raising.

## Token rate limiting

Live

The [LLM Token Rate Limit](https://docs.mulesoft.com/gateway/latest/policies-included-llm-token-rate-limit)
policy caps the tokens a consumer may spend per window. On every governed
response DDK reads the remaining budget into
[`donkey.budget`](https://docs.donkey-kit.dev/budget.md), so an agent can `pace()` against it instead of
discovering the limit by failing. When the window is spent, the gateway answers
`429` and DDK raises `TokenBudgetExceeded` with `.retry_after` derived from the
reset.

The transport never retries it: the same window would refuse again. Wait for the
reset, or register a [refusal handler](https://docs.donkey-kit.dev/refusal-handlers.md) such as
`donkey.on(TokenBudgetExceeded).wait_for_reset(max_wait="45m")`. The gateway has
no endpoint that reports the budget, so it is
[observed in-band](https://docs.donkey-kit.dev/budget.md#budget-is-observed-in-band) and is only as fresh as
the last call.

Rehearse it with `donkey mock --scenario budget:limit=20000,window=60s`
([scenario](https://docs.donkey-kit.dev/simulator.md#budgetlimittokenswindowduration)).

## Request rate limiting

Live

The Rate Limiting policy caps requests rather than tokens. DDK reads its window
into `donkey.budget.requests`, and a refusal raises `RequestRateLimitExceeded`,
distinguished from a token refusal by its `x-ratelimit-*` headers. Pass
`window=exc.window` to `wait_for_reset()` so the wait follows the window that
refused. See [Request window limits](https://docs.donkey-kit.dev/budget.md#request-window-limits) for how
`pace()` behaves on a small request window.

Rehearse it with `donkey mock --scenario request_limit:limit=3,window=60s`
([scenario](https://docs.donkey-kit.dev/simulator.md#request_limitlimitrequestswindowduration)).

## Model wallet

Live

A wallet-backed proxy authenticates with an IdP-issued JWT plus a client ID
instead of a client secret, and spends from a wallet budget attached by the
platform. Configure the auth mode as described in
[JWT / model-wallet auth mode](https://docs.donkey-kit.dev/reference/configuration/auth.md#jwt--model-wallet-auth-mode).

Wallet state is not reported in-band: a `200` carries no limit or remaining
value, so `donkey.budget` cannot show it and there is no warning before the
wallet runs out. The first signal is the refusal, a `429` that DDK raises
today as `TokenBudgetExceeded` with the gateway's `retry-after` as
`.retry_after`. Handle it as you would a token-window refusal.

## PII detection

Live

The [LLM PII Detection](https://docs.mulesoft.com/gateway/latest/policies-included-llm-pii-detection)
policy refuses a prompt that carries personal data. DDK raises `PIIDetected`,
with the flagged entity types on `.entities`. Retrying sends the same data and
gets the same refusal, so redact or remove the flagged values first. A
[refusal handler](https://docs.donkey-kit.dev/refusal-handlers.md) can rewrite and resend as a new governed
request; the gateway judges it from scratch.

Rehearse it with `donkey mock --scenario pii_block:every=5`
([scenario](https://docs.donkey-kit.dev/simulator.md#pii_blockeveryn)) or `donkey.simulate(PIIDetected)`.

## Injection Protection and Regex Prompt Guard

Live

Two policies refuse prompts that look like an attack, and both surface as
`PromptInjectionBlocked`. `.policy` tells them apart:

- [Injection Protection](https://docs.mulesoft.com/gateway/latest/policies-included-injection-protection)
  answers `400` and marks the block with the `x-injection-protection: blocked`
  header, so an ordinary malformed `400` stays an ordinary refusal.
  `.policy` is `"prompt-injection-protection"`. This shape was captured live;
  it is the Injection Protection row of the ledger's §4 shape table.
- [Regex Prompt Guard](https://docs.mulesoft.com/gateway/latest/policies-included-regex-prompt-guard)
  answers `403` with the patterns that matched. `.policy` is
  `"regex-prompt-guard"`.

Review and sanitise the untrusted input that reached the prompt, or ask the
instance owner to adjust the policy. Rehearse it with
`donkey mock --scenario injection:on-pattern="ignore previous"`
([scenario](https://docs.donkey-kit.dev/simulator.md#injectionon-patternsubstring)).

## Content safety and guardrails

Live

Three guardrail policies send the prompt to an external moderation service and
refuse what it flags:
[Azure Content Safety](https://docs.mulesoft.com/gateway/latest/policies-included-azure-content-safety),
[Amazon Bedrock Guardrails](https://docs.mulesoft.com/gateway/latest/policies-included-bedrock-guardrails)
and [Google Model Armor Guardrails](https://docs.mulesoft.com/gateway/latest/policies-included-google-model-armor-guardrails).
All three answer `403` with a vendor-specific `…-action: reject` header, and DDK
raises `ContentSafetyBlocked` with `.provider` naming the vendor and
`.categories` listing what was flagged. Each of the three blocks was captured
live; the Bedrock capture is in the content-safety row of the ledger's §4 shape
table.

The same header on a `503` is not a block. It means the policy failed closed
because its moderation service was unreachable, and DDK raises
`UpstreamModelError`, which is retryable. Revise the flagged content, or adjust
the categories and thresholds in API Manager (for Model Armor, the template's
filters in Google Cloud).

## CrowdStrike AIDR Guardrails

Policy-source

The [CrowdStrike AIDR Guardrails](https://docs.mulesoft.com/gateway/latest/policies-included-crowdstrike-aidr-guardrails)
policy works like the guardrails above, and `classify()` types its block as
`ContentSafetyBlocked` too. Its shape is read from the policy source, not
captured from a live gateway: DDK types it under
[ADR 0016](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/adr/0016-policy-source-evidence-class.md),
and the live capture is still open. Adjust what it blocks in the collector's
AIDR policy in the Falcon console.

## Agent Kill Switch

Live

An administrator can kill an agent at the gateway. Every call it makes is then
refused with `403`, and DDK raises `AgentKilled`. The upstream is never called.
It is never retried, it stops [`donkey.budget.pace()`](https://docs.donkey-kit.dev/budget.md) loops, and the
agent stays blocked until an administrator restores it. Treat it as a stop
signal: end the run cleanly instead of retrying.

## OAuth2 OBO step-up (CIBA)

Live

With OAuth2 OBO Credential Injection and CIBA enabled, the gateway asks the
user to approve the call on another device before it forwards it. While it
waits, DDK raises `GatewayApprovalPending` with `.retry_after`. That is not a
credential failure, and the transport never retries it for you: re-send the
same request after `.retry_after`, and it goes through once the user approves.
If the user refuses, DDK raises `GatewayApprovalDenied`. The gateway caches a
denial, so retrying gets the same answer until it expires.

See [Gateway step-up](https://docs.donkey-kit.dev/hitl/gateway-step-up.md#gateway-step-up) for how the step-up behaves and
[Identity](https://docs.donkey-kit.dev/identity.md) for acting on behalf of a user. Rehearse it with
`donkey mock --scenario ciba:pending=2,then=approve`
([scenario](https://docs.donkey-kit.dev/simulator.md#cibapendingnthenapprovedeny)).

## Model-based routing and fallback

Live

The model-based routing policy reads the model from the request and picks the
provider, failing over to a fallback route when one is configured. On every
response, DDK reports what the gateway did on
[`donkey.last_call`](https://docs.donkey-kit.dev/reference/last-call.md#routing--fallback): `served_provider`,
`served_model`, `routing_type`, `fallback` and `substituted`. A substitution is
passive by default. `Donkey.from_env(on_model_substitution="raise")` turns it
into a `ModelSubstituted` error.

When the gateway cannot route the request at all, it answers `400` before any
upstream call and DDK raises `ModelNotRoutable`. That happens for a bare model
name on a proxy with more than one provider (use `provider/model`, for example
`openai/gpt-5-mini`), a missing model, or a provider with no route and no
fallback. `.model` and `.provider` say which. See
[Routing & fallback](https://docs.donkey-kit.dev/frameworks/routing.md) for how DDK's retries
stay out of the way of a gateway failover.

## Semantic routing

Live

A semantic-routing proxy picks the provider from the topic the prompt matches.
`last_call.routing_type` is `"Semantic"`, and `last_call.matched_topic` and
`.routing_score` say which topic won and how closely. Both stay `None` on a
model-based proxy. See
[Semantic caching & semantic routing](https://docs.donkey-kit.dev/frameworks/routing.md#semantic-caching--semantic-routing-live).

## Semantic caching

Live

The semantic-caching policy can answer a request from a stored completion when
a similar prompt was seen before. DDK caches nothing itself. It lets you steer
the gateway's cache per block with
[`donkey.cache(...)`](https://docs.donkey-kit.dev/budget.md#semantic-cache-steering) and reports the outcome on
[`last_call.cache_status`, `.cache_score` and `.cache_hit`](https://docs.donkey-kit.dev/reference/last-call.md#semantic-cache).
A hit replays the original `usage` and never advances the budget.

## Unrecognised refusals

A refusal that matches no known discriminator falls through to the generic
`PolicyViolation`. It is terminal, and `.response` keeps the status, headers and
body. If the refusal comes from your own policy, teach `classify()` its shape
with `classify.register()` ([Custom policies](https://docs.donkey-kit.dev/custom-policies.md)), and it becomes
a typed exception of your own. If it is a gateway policy DDK should know,
[file an issue](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues)
with the status, headers and body.

## Next steps

- [Typed refusals](https://docs.donkey-kit.dev/errors.md): every exception, its discriminator and its retry rule.
- [Budget & pacing](https://docs.donkey-kit.dev/budget.md): pace against the token and request windows.
- [Refusal handlers](https://docs.donkey-kit.dev/refusal-handlers.md): wait out a window or rewrite and resend.
- [Local simulator](https://docs.donkey-kit.dev/simulator.md): rehearse each refusal before it reaches production.

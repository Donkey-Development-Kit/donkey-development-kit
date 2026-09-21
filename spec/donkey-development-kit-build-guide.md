# Donkey Development Kit — Phase 1, 2, 3 Build Guide

**Purpose of this document:** a feature-by-feature explanation of what to build, in the order to build it, with a concrete scenario for each so the team understands *why* it exists and *what "done" looks like*. Written for the people who will build it and the people who need to approve it.

**Date:** 4 September 2026 · Companion to the slide deck of the same name — section numbers match the slides.

> **Note on API names.** Every Python signature below is a *proposal* to give the team something concrete to react to. Adjust freely. What must not change is the behaviour and the acceptance criteria.

> **Note on gateway contracts.** Header and body shapes quoted below (`x-token-*`, `pii_detected`, `x-injection-protection`) come from the public Omni Gateway policy docs (v1.11–v1.13). Re-verify against the current docs version before hard-coding anything — that is exactly the repo's existing verification discipline, keep it.

---

## Three running scenarios

Use these in every team discussion. Every feature below is explained against at least one of them.

| Scenario | Description | Why it stresses the gateway |
|---|---|---|
| **A — Support triage agent** | LangGraph agent, 20–40 LLM calls per ticket, reads customer emails, calls a CRM tool, drafts a reply. Runs in production, 2,000 tickets/day. | Hits PII policy constantly (emails contain PII), burns budget fast, needs audit trail per ticket, needs human approval before sending a refund. |
| **B — Nightly enrichment batch** | Plain Python script, no framework, enriches 50,000 product records overnight against a governed model. | Hits token budget mid-run, must pace itself, must resume after the budget window resets, no human present to intervene. |
| **C — Internal HR copilot** | Slack bot, low volume, high sensitivity. Answers policy questions using an internal RAG tool exposed via MCP. | Strict content-safety policy, tool allow-list, per-user identity must reach the gateway, kill-switch must actually stop it. |

---

## How the LLM wrapper fits (kept, reframed)

You asked to keep the wrapper. Keep it — but explain it to the team correctly, because the framing determines what gets built.

**Wrong framing:** "The wrapper is how you call the Omni Gateway."
Any stock OpenAI client with `base_url` + headers does that. Built this way, the wrapper is a dependency with no payoff, and reviewers will say so.

**Right framing:** "The wrapper is the skeleton. It is the *single point in the process* where every request enters and every response leaves — so it is the only place where budget headers, error classification, correlation IDs, cost tags, OTel spans, simulation, and refusal handlers can all attach without the developer wiring each one."

The repo's own architecture already says this (`DonkeyAsyncClient` is "one transport and one header-injection point"). The value of the wrapper is therefore exactly the sum of what the six-piece minimum hangs on it. Build the six pieces and the wrapper is justified; skip them and it is not.

**Team one-liner:** *"We are not selling the client. We are selling everything the client makes automatic."*

---

# PHASE 1 — Earn the install (0–3 months)

**Goal:** a developer who tries the SDK for 15 minutes finds three things they cannot get from `base_url` + headers, and one of them saves them from a production incident.

**The six-piece minimum** is items 1.2–1.7. Items 1.1, 1.8–1.10 are the skeleton, the adapter, and the on-ramps that make the six pieces reachable.

---

## 1.1 The LLM client (skeleton)

**What it is.** `Donkey` object owning one `httpx.AsyncClient` subclass. Every governed call goes through it. It returns native framework objects, never wrappers.

**What it does today that stays:** base URL (no `/v1`), `client_id`/`client_secret` headers, streaming, config precedence (`kwargs → env → .donkey-kit.toml`), report-all-missing-config-at-once.

**What changes in Phase 1:** it becomes the attachment point for 1.2–1.7. Concretely, the transport gains four hooks that the other features implement:

```python
class DonkeyAsyncClient(httpx.AsyncClient):
    # called before send: correlation ID, cost tags, OTel span start
    async def _on_request(self, request): ...
    # called after receive: budget parse, OTel span end, classify()
    async def _on_response(self, request, response): ...
    # called when classify() yields a PolicyViolation
    async def _on_refusal(self, violation): ...
    # test-only: replaced by simulate() / the local simulator
    _transport: httpx.AsyncBaseTransport
```

**Scenario.** Scenario B's script is 40 lines. The developer writes:

```python
donkey = Donkey.from_env()
client = donkey.openai()          # a real openai.AsyncOpenAI, nothing to learn
```

and gets 1.2–1.7 without a further line. That is the whole pitch.

**Acceptance.** `donkey.openai()` returns `openai.AsyncOpenAI`; `type(client).__module__ == "openai"`. Importing `donkey_kit` with no extras installed imports nothing from any framework (existing `base-only` CI job).

**Effort:** S (mostly exists).

---

## 1.2 Typed refusals aligned to the documented contract — piece 1 of 6

**What it is.** `classify()` turns a gateway rejection into a typed exception with a required `remediation` string, and the transport treats every `PolicyViolation` as **terminal — never retried**.

**The documented contracts to align to** (re-verify):

| Policy | Status | Discriminator | Exception |
|---|---|---|---|
| Token rate limit | 429 | headers `x-token-limit`, `x-token-remaining`, `x-token-reset`; body empty | `TokenBudgetExceeded` |
| PII detection | 403 | body `{"error": {"type": "pii_detected", ...}}`, **no** `www-authenticate` | `PIIDetected` |
| Injection protection | 400 | header `x-injection-protection: blocked` | `PromptInjectionBlocked` |
| Content moderation / federated guardrails | *under-documented* | fall through | generic `PolicyViolation` whose message says the shape is unconfirmed |
| Upstream provider 4xx | 4xx non-429 | provider's nested `code`/`type`/`param` | `UpstreamRequestError` (terminal, not a policy refusal) |
| Upstream 5xx | 5xx | — | `UpstreamModelError` (retryable) |

**Why it matters — Scenario A.** A customer email contains a card number. The gateway returns 403 `pii_detected`. Without the SDK, `langchain_openai` sees a 403, classifies it as an auth problem, and the agent's error handler logs "authentication failed" — the on-call engineer rotates credentials at 2 a.m. for nothing. With the SDK the agent catches `PIIDetected`, reads `.remediation` ("input contained PII of type CREDIT_CARD; mask before resubmitting"), masks the field, and continues.

**Why it matters — Scenario B.** Budget exhausted → 429 with empty body and no `Retry-After`. The stock `openai` client retries 429s with exponential backoff by default. It will retry a *policy refusal* several times, each retry counting against the same exhausted window. With the SDK the transport raises `TokenBudgetExceeded` on the first 429 and never retries.

**API sketch.**

```python
try:
    reply = await client.chat.completions.create(...)
except PIIDetected as e:
    e.remediation          # required, human-readable next step
    e.pii_types            # from the nested error object
    e.request_id           # correlation, joins the gateway audit log
    e.raw_response         # for inspection, never for parsing
except TokenBudgetExceeded as e:
    e.budget.reset_at      # see 1.3
```

**Acceptance.**
- Every row above has a fixture in `tests/fixtures/rejections/` captured from a live gateway, with the docs URL and version recorded in the fixture header.
- A test proves `openai`'s built-in retry does **not** fire on 429 when going through `DonkeyAsyncClient` (mock transport counts requests; assert exactly one).
- A test proves a 403 with `pii_detected` does **not** raise `AuthError`.
- `PolicyViolation.__init__` fails if `remediation` is empty.

**Effort:** S. Most exists; the work is re-aligning to the now-public contracts and adding the negative tests.

---

## 1.3 Budget as a first-class object + rate-limit-aware pacing — piece 2 of 6

**What it is.** Every response that carries `x-token-*` headers updates a `Budget` object on the `Donkey` instance. The developer never parses headers. A `pace()` helper uses it to slow down *before* hitting the wall.

```python
donkey.budget.limit          # int, tokens per window
donkey.budget.remaining      # int, from last response
donkey.budget.reset_at       # datetime, from x-token-reset (ms) — converted, not raw
donkey.budget.observed_at    # when we last saw headers (staleness)
donkey.budget.fraction_used  # 0.0–1.0

await donkey.budget.wait_for_reset()          # sleeps until reset_at
async with donkey.budget.pace(reserve=0.10):  # raises BudgetReserveReached at 90 %
    ...
```

**Why it matters — Scenario B.** 50,000 records, budget window resets every hour. Without this, the script runs flat out, hits 429 at record 31,000, crashes, and someone re-runs it in the morning from record 0 — spending the budget twice. With this:

```python
for batch in chunks(records, 200):
    while True:
        try:
            async with donkey.budget.pace(reserve=0.05):
                await enrich(batch)
        except BudgetReserveReached as exc:
            if exc.reset_at is None:
                raise  # no reset time means waiting cannot make progress
            await donkey.budget.wait_for_reset()
            continue
        break
    checkpoint(batch)
```

After `reset_at`, the old observation is stale, so `pace()` lets the retry
through and its response refreshes the in-band budget. The job finishes by
itself, overnight, with no human. If a partial observation reaches the reserve
without a `reset_at`, the loop re-raises `BudgetReserveReached` instead of calling
`wait_for_reset()` and spinning at zero delay. The caller can then preserve its
last checkpoint and escalate the incomplete budget signal without crossing its
reserve.

**Why it matters — Scenario A.** A dashboard shows `fraction_used` per agent. The support agent's owner sees it climbing at 14:00 and requests an increase before the 16:00 peak instead of after the outage.

**Honest limitation to tell the team.** The gateway exposes budget only *in-band* (headers on a response). There is no "GET remaining budget" endpoint. So `remaining` is only as fresh as the last call, and a brand-new process knows nothing until its first request. This is one of the five upstream gaps (see Phase 3). The SDK should expose `observed_at` so nobody mistakes stale data for live data.

**Acceptance.**
- `x-token-reset` in milliseconds is converted correctly (fixture with a known value; assert `reset_at` to the second).
- `pace()` raises before the request that would cross the reserve, not after a 429.
- After `reset_at`, `pace()` lets one retry through so its response can refresh the in-band budget; the documented `pace()` → `wait_for_reset()` → retry loop makes progress without a manual observation.
- When the reserve is reached but `reset_at` is unknown, the documented loop re-raises `BudgetReserveReached` after one attempt instead of retrying at zero delay.
- Budget object is per-`Donkey`, not global; two `Donkey` instances with different credentials do not share state.

**Effort:** S.

---

## 1.4 Local gateway simulator — piece 3 of 6

**What it is.** `donkey mock` starts a local HTTP server that behaves like the Omni Gateway LLM Proxy for *the failure paths*: it replays the captured rejection fixtures from 1.2 on demand, and forwards happy-path requests to a stub model (or, optionally, to a real upstream key for local dev).

```bash
donkey mock --port 8080 \
  --scenario pii_block:every=5 \
  --scenario budget:limit=20000,window=60s \
  --scenario injection:on-pattern="ignore previous"
```

Then any client — SDK or not — is pointed at `http://localhost:8080`.

**Why it matters — Scenario A.** Nobody can make the production gateway emit a PII block on cue. So today the branch of the support agent that handles `PIIDetected` has *never executed* before it executes on a real customer. With the simulator, `pii_block:every=5` makes every fifth call fail, and the agent's masking logic is exercised in the developer's terminal before the PR is opened.

**Why it matters — Scenario B.** `budget:limit=20000,window=60s` shrinks an hour-long window to one minute. The pacing and resume logic from 1.3 is tested end-to-end in ninety seconds instead of "we'll see tonight."

**Why it matters for adoption.** This is the demo. "Develop against a governed gateway without having a gateway" is the sentence that gets an AI engineer to install the package. It is also the one feature that is impossible to reproduce with `base_url` + headers, because the value is on the server side.

**Design rules.**
- Fixtures are the *same files* used by `classify()` tests. One source of truth; if the contract drifts, both fail together.
- Emit the same `x-token-*` headers on happy-path responses so 1.3 works against the mock.
- Ship as `pip install donkey-kit[local]`; no Docker required.
- Must be clearly labelled in every response header (`x-donkey-simulator: true`) so it can never be mistaken for a real gateway in a log.

**Acceptance.**
- The full conformance suite (1.5) passes against the simulator.
- A stock `openai.OpenAI(base_url="http://localhost:8080", default_headers=...)` — *not* the SDK — receives byte-identical rejection shapes. This proves the simulator is honest.
- Scenario scripting is documented with three worked examples matching Scenarios A, B, C.

**Effort:** M.

---

## 1.5 `simulate()` + public conformance pytest plugin — piece 4 of 6

**What it is.** Two test-time tools.

`simulate()` — an in-process context manager that makes the next N calls through a `Donkey` return a chosen refusal, without any server:

```python
async def test_agent_masks_pii(donkey):
    with donkey.simulate(PIIDetected, pii_types=["CREDIT_CARD"], times=1):
        result = await triage_agent.run(ticket_with_card_number)
    assert "****" in result.draft_reply
```

The pytest plugin — `pip install donkey-kit[test]` exposes a `donkey` fixture (pre-wired to the simulator or to `simulate()`), and a **conformance suite** the customer runs against *their own agent*:

```bash
pytest --donkey-conformance --agent=my_app.agent:build
```

which runs scenarios like: *does your agent retry a `TokenBudgetExceeded`? (it must not)* · *does it swallow `PIIDetected` as a generic exception?* · *does it propagate the correlation ID into its own logs?* · *does it still work when budget headers are absent?*

**Why it matters — Scenario A.** The support-agent team gets a failing conformance test on day one that says "your agent retried a budget refusal 3 times." That is a bug they did not know they had, found in CI, phrased in a way nobody argues with. This is the moment the SDK becomes trusted.

**Why it matters — Scenario C.** The HR bot has a strict allow-list. `simulate(ContentSafetyBlocked)` proves the bot answers "I can't help with that" rather than leaking the raw gateway error into Slack.

**Difference between 1.4 and 1.5, for the team.** The simulator is a *server* for manual dev, demos, and integration tests against any client. `simulate()` is *in-process* for fast unit tests. They share fixtures.

**Acceptance.**
- `simulate()` works with no network and no server.
- Plugin is published as a pytest entry point; `pytest --donkey-conformance` prints a table of scenario → pass/fail/exempt.
- Exemptions must be asserted in code (`KNOWN_LIMITATIONS`), never silently skipped — the repo already has this rule for its own adapters; extend it to customers' agents.

**Effort:** M.

---

## 1.6 OpenTelemetry GenAI instrumentation — piece 5 of 6

**What it is.** Every governed call produces an OTel span following the GenAI semantic conventions (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, …) **plus** Donkey-specific attributes for the governance layer:

```
donkey.policy.decision      = allow | refuse
donkey.policy.type          = pii_detected | token_budget | injection | …
donkey.budget.remaining     = 18450
donkey.correlation_id       = …
donkey.cost.team            = support          (from 1.7)
```

Export goes wherever the developer already sends spans (OTLP). Nothing is Anypoint-specific in the emit path.

**Why it matters — Scenario A.** The team already runs Langfuse (or Datadog, or Phoenix). Tomorrow they see "policy refusals per hour by type" and "tokens per ticket" in the dashboard they already have, with zero new tooling. That is the feature that makes platform teams say yes.

**Why it matters — Scenario C.** Audit asks "which user's request was refused for content safety on Tuesday?" The span carries `enduser.id` (1.7), `donkey.policy.type`, and the correlation ID that joins to the gateway's own audit log. Answer in one query.

**Honest caveats to tell the team.**
- The GenAI conventions are still *Development* status. Names can change. **Pin** the semconv version and **dual-emit** (`gen_ai.*` at the pinned version plus a stable `donkey.*` namespace we control) so customer dashboards do not break when upstream renames.
- Whether Anypoint Monitoring / Agent Visualizer ingests OTLP GenAI spans is *not publicly documented*. Do **not** promise "shows up in Agent Visualizer" until someone inside confirms it. Ship "exports OTLP" and let the sink be the customer's choice. If the internal answer is yes, that becomes a headline feature in Phase 2.

**Acceptance.**
- Zero-config: `Donkey.from_env()` + `OTEL_EXPORTER_OTLP_ENDPOINT` set → spans appear. No SDK-specific env var needed.
- A refused request still produces a span, with `donkey.policy.decision=refuse` and `otel.status_code=ERROR`.
- Streaming responses produce one span with token counts filled at stream end.
- Instrumentation is opt-out with a single flag and adds < 1 ms overhead (benchmark in CI).

**Effort:** M.

---

## 1.7 Correlation IDs + cost-attribution tags — piece 6 of 6

**What it is.** Two small header features that make the enterprise story true.

*Correlation.* Every request gets a per-call ID and, optionally, a per-run ID the developer sets once (`donkey.run(id=ticket_id)`). Both are injected as headers, attached to every exception (1.2), every span (1.6), and logged. The gateway's audit log carries the same ID, so client-side and gateway-side records join.

*Cost attribution.* A small, fixed set of tags — `team`, `project`, `env`, `enduser.id` — set once on the `Donkey` (or per run) and injected as headers on every call. They appear in spans and are available for the gateway to bill/report on.

```python
donkey = Donkey.from_env(team="support", project="triage-v2", env="prod")

async with donkey.run(id=ticket.id, enduser_id=agent_user.id):
    await triage_agent.run(ticket)
```

**Why it matters — Scenario A.** Finance asks "what did the support agent cost last month versus the HR bot?" Without tags, both agents share one `client_id` and the answer is "we don't know." With tags, it is a group-by.

**Why it matters — Scenario C.** Compliance asks "prove the HR bot's answer to user X on date Y went through the content-safety policy." The correlation ID on the Slack message's log line joins to the gateway record. That is what an EU AI Act Article 12 log request looks like in practice.

**Honest note.** Which header names the gateway actually reads for attribution must be verified internally (the repo's `Unverified(...)` mechanism is exactly for this — use it and keep the override in config). Until verified, the tags still have full value in the OTel spans, which the SDK controls end to end.

**Acceptance.**
- Per-run ID propagates through LangGraph nodes without the developer threading it (contextvar-based).
- Every `DonkeyError` exposes `.correlation_id` and it matches the header that was sent.
- Tags are validated (fixed keys, max length) so nobody stuffs a JSON blob into a header.

**Effort:** S.

---

## 1.8 One deep adapter (LangGraph) + raw client

**What it is.** Cut the adapter roster from eight to **one deep** (LangGraph, via `langchain_openai.ChatOpenAI` over the Donkey transport) plus the **raw** `openai`/`httpx` client. Deep means: correlation IDs flow through graph nodes automatically, `interrupt()` and refusals compose, the conformance suite runs against a real LangGraph example app, and there is a full worked tutorial.

**Why cut.** Eight adapters at 0 users is 8× the surface for every contract change (and the contract *will* change — it changed twice in the last three docs releases). One adapter done properly is a better demo than eight done thinly. The other seven come back in Phase 2 and beyond **by demand**, one at a time, each with its own conformance run.

**What to keep from the current eight.** Their `connection_kwargs()` accessors — the three-line escape hatch — can stay for every framework at near-zero cost, documented as "supported via connection kwargs, not conformance-tested."

**Why LangGraph first.** Largest Python agent-framework install base; its `interrupt()` primitive is what Phase 2 HITL builds on; its node structure makes correlation-ID propagation a visible win.

**Acceptance.** `langgraph-support-triage/` in the [companion demos repo](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos) implements Scenario A end-to-end against the simulator and passes conformance.

**Effort:** S to cut, M to deepen.

---

## 1.9 Decorators + minimal CLI

**What it is.** Two on-ramps so the six pieces are reachable in one line.

```python
@donkey.governed(team="support")          # wraps any async fn: run-ID, tags, span, refusal → typed
async def handle_ticket(ticket): ...

@donkey.tool                               # marks a function as a governed tool (Phase 2 scanner reads this)
async def lookup_crm(customer_id: str) -> dict: ...
```

```bash
donkey init          # writes .donkey-kit.toml, prints what env vars are missing
donkey doctor        # checks creds, reaches the gateway, prints policies it can observe, budget state
donkey mock          # 1.4
donkey test          # 1.5 conformance
```

**Why it matters.** `donkey doctor` alone shortens "why doesn't this work" from an afternoon to thirty seconds. Decorators are how AWS AgentCore made identity feel free; copy the pattern.

**Acceptance.** `donkey doctor` distinguishes "wrong credentials" from "wrong URL" from "credentials fine, model not in allow-list" — each with the remediation string from 1.2.

**Effort:** S (CLI) + S (decorators).

---

## 1.10 Docs, examples, `llms.txt`

**What it is.** Docs site with: 15-minute quickstart against the simulator (no gateway needed), one page per scenario A/B/C with full code, a "refusal cookbook" (one page per exception type: what it means, what to do), per-framework `connection_kwargs()` pages, and an `llms.txt` so Cursor/Claude Code can read the docs.

**Why it matters.** Vercel AI SDK and AgentCore both show the docs *are* the product for adoption. And the target user increasingly asks their coding assistant first; `llms.txt` is how the SDK shows up in that answer.

**Effort:** M, ongoing.

---

## Phase 1 exit criteria

- On PyPI, semver, changelog.
- The six pieces pass against both the simulator and a live sandbox.
- Scenario A demo runs end-to-end in under 15 minutes from `pip install`.
- 500+ stars and ≥ 3 external contributors within a quarter of launch — below that, stop and reassess before Phase 2.

---

# PHASE 2 — Differentiate (3–6 months)

**Goal:** the SDK does things no generic gateway SDK does, because it understands *this* gateway's governance model — and it integrates with, never duplicates, what MuleSoft has shipped.

---

## 2.1 Typed refusal reaction handlers

**What it is.** Declarative "what to do when refused," registered once, applied everywhere the `Donkey` is used.

```python
donkey.on(TokenBudgetExceeded).wait_for_reset(max_wait="45m")
donkey.on(PIIDetected).call(mask_and_retry, max_times=1)
donkey.on(ContentSafetyBlocked).fallback(model="internal-safe-model")
donkey.on(PolicyViolation).escalate(to=hitl_queue)     # catch-all
```

**Why it matters — Scenario B.** The resume logic from 1.3 becomes one line instead of a try/except in every loop.

```python
# BEFORE — 1.3: recovery written inside every loop that touches the model
for batch in chunks(records, 200):
    while True:
        try:
            async with donkey.budget.pace(reserve=0.05):
                await enrich(batch)
        except BudgetReserveReached as exc:
            if exc.reset_at is None:
                raise
            await donkey.budget.wait_for_reset()
            continue
        break
    checkpoint(batch)

# AFTER — 2.1: recovery declared once, runs at the transport
donkey.on(TokenBudgetExceeded).wait_for_reset()

for batch in chunks(records, 200):
    await enrich(batch); checkpoint(batch)
# the loop never sees the 429 at all
```

**Nuance to state when presenting this.** The two are not identical. The 1.3 version *paces* before the wall using `reserve`, so it never triggers a 429; the 2.1 handler *reacts* after the 429 lands and retries the same request from inside the transport. Both are correct. In practice, combine them — keep `pace()` in the loop and let the handler catch the rare case pacing misses.

**Why it matters — Scenario A.** Twelve LangGraph nodes call the model. Without handlers, each node needs the same PII try/except. With handlers, it is defined once and applied at the transport.

**Rule to enforce.** Handlers *react*; they never *decide policy*. A handler cannot un-refuse a request. If someone proposes `donkey.on(PIIDetected).ignore()`, that is client-side enforcement by another name — reject it in review.

**Effort:** M.

---

## 2.2 Classification registry for custom policy types

**What it is.** Customers write custom gateway policies (PDK) that emit their own error types. The SDK lets them register a mapping so the custom refusal becomes a first-class typed exception with a remediation string, without forking the SDK.

```python
class PCIViolation(PolicyViolation):
    remediation = "Card data must be tokenised via the Vault API before this call."

donkey.classify.register(
    match=dict(status=403, error_type="acme_pci_block"),
    raises=PCIViolation,
)
```

**Why it matters — Scenario C.** A bank's HR bot has a custom "no salary figures outbound" policy. Today its refusal is a generic 403 that looks like an auth error. With the registry it is `SalaryLeakBlocked`, with a remediation the bot can show the user.

**Why it is safe.** Nothing is enforced client-side. The gateway still decides. The SDK only names the decision.

**Effort:** S–M.

---

## 2.3 Human-in-the-loop normalisation + step-up routing

**What it is.** One SDK vocabulary for "pause and ask a human," mapped onto each framework's native primitive, with an option to route the approval through the gateway's identity layer.

| Framework / protocol | Native primitive the SDK maps to |
|---|---|
| LangGraph | `interrupt()` / `Command(resume=…)` |
| OpenAI Agents SDK | tool-approval / guardrail hooks |
| Google ADK | before/after tool callbacks |
| Strands | hooks |
| MCP | elicitation |
| Omni Gateway | Trusted Agent Identity step-up (MFA) — verify internally |

```python
@donkey.tool(approval="required", risk="financial")
async def issue_refund(ticket_id: str, amount: float): ...
```

When the agent calls `issue_refund`, the SDK raises `ApprovalRequired` (or triggers the framework's interrupt), records the pending approval with the correlation ID, and resumes on `donkey.approvals.resolve(id, approved_by=…)`.

**Why it matters — Scenario A.** Refunds over €100 need a human. Today that is bespoke code per team per framework. With this, it is a decorator argument, the pending approval is visible in the span (1.6), and the approver's identity lands in the audit trail (1.7).

**Honest note.** Every framework already has HITL. The value is *normalisation* (one vocabulary), *auditability* (correlation + approver in the trail), and *gateway routing* (step-up MFA) — not a new mechanism. Do not build a queue or an approval UI; integrate with what the customer has (Slack, ServiceNow, LangGraph's checkpointer).

**Effort:** L.

---

## 2.4 Thin identity helpers (on-behalf-of, RFC 8693)

**What it is.** Small helpers that acquire and attach a user-scoped token for the gateway's Trusted Agent Identity, so the gateway can enforce per-user policy.

```python
async with donkey.as_user(id_token=slack_user_oidc_token):
    await hr_bot.answer(question)      # gateway sees the user, not just the service
```

**Why it matters — Scenario C.** The HR bot must not answer a manager's salary question for a different manager's report. That decision belongs to the gateway (it has the identity policy); the SDK's only job is to get the user's token onto the request correctly.

**Honest boundary.** Trusted Agent Identity is a shipped MuleSoft feature. The SDK does the token-exchange plumbing (RFC 8693) and header placement. It does **not** implement authorisation logic. If the team proposes "check the user's role in the SDK," that is client-side enforcement — no.

**Effort:** M, dependent on verifying the exact token-exchange endpoint and header the gateway expects.

---

## 2.5 In-repo scanner + GitHub Action → Exchange

**What it is.** `donkey scan` walks a repository, finds everything marked `@donkey.tool`, MCP server definitions, and agent entry points, and produces a manifest (`donkey.yaml` / A2A agent card). `donkey publish` registers the manifest with Anypoint Exchange / Agent Registry. A GitHub Action runs both on every merge to `main`.

**Why it matters — Scenario A.** The support agent has six tools. Today the Agent Registry knows about the agent only if someone registers it by hand, and the tool list is stale within a week. With the Action, every merge updates the registry from the code — the registry becomes a *consequence* of the code, not a chore.

**Honest positioning — this is important for the team.** MuleSoft's **Agent Scanners** already discover agents from Agentforce, Bedrock, Vertex AI, and Copilot Studio at *runtime*. This feature is the *design-time / CI* complement: it registers agents built in plain Python that no cloud scanner can see. Position it as "the Agent Scanner for your git repo." If it is pitched as a competitor to Agent Scanners, it will be shut down internally — and rightly.

**Dependencies.** The Exchange publish API is currently `blocked("…")` in the repo. It can now be verified internally — do that first; do not guess.

**Effort:** L.

---

## 2.6 Kill-switch awareness

**What it is.** When the Agent Kill Switch fires for this agent, the gateway rejects requests. The SDK recognises that rejection (once the shape is verified), raises `AgentKilled` (terminal, never retried, never handled by 2.1 fallbacks), stops any in-flight `pace()` loop, flushes spans, and exits cleanly.

**Why it matters — Scenario C.** Security kills the HR bot at 15:02. Without this, the bot's 30 retry loops keep hammering the gateway for ten minutes and Slack fills with stack traces. With this, the bot posts "I've been paused by an administrator" once and stops.

**Effort:** S (after shape verification).

---

## 2.7 MCP tool discovery (Exchange → MCP)

**What it is.** `donkey.tools.discover()` lists governed MCP tools the agent is allowed to use, returns them as native framework tools, and filters by the gateway's allow-list.

**Why it matters — Scenario C.** The RAG tool is exposed via MCP behind the gateway. The bot should get *only* the tools it is allowed to call, from the registry, not from a hard-coded list that drifts.

**Dependencies.** `blocked("…")` today; verify the endpoint internally. Overlaps MuleSoft's MCP Bridge — position as the *client-side consumption* of governed MCP, which the Bridge does not provide.

**Effort:** L.

---

## 2.8 Second adapter (by demand)

Add one more deep adapter chosen by what users ask for in Phase 1 issues — likely OpenAI Agents SDK or LlamaIndex. Same bar as LangGraph: conformance, demo, tutorial.

**Effort:** M.

---

## 2.9 A2A-ready agents — `serve`, `expose`, `dev`

**The question from the team.** *"A2A needs an HTTP listener. We have the gateway. Can the CLI embed Omni Gateway ingress into the code so the agent is A2A-ready without running its own server?"*

**Honest answer: no to the literal idea, yes to what it is actually asking for.**

**Why the literal version is rejected.** Omni Gateway is an Envoy-based data plane whose policies compile to WASM; it is a separate process run managed or self-managed, not a library. You cannot `import` it any more than you could import nginx. More importantly, you would not want to: the moment the enforcement point lives inside the agent's process, the agent's own code can bypass it — the client-side enforcement anti-pattern this whole document exists to prevent. The gateway's value depends on being *outside* the thing it governs.

**Be precise about what A2A needs.** Someone must accept the socket. An A2A agent is a server: it serves an agent card at a well-known path and answers JSON-RPC task calls. That listener can be hidden behind one line, but it cannot be removed. What *can* be removed is everything painful about it — TLS, auth, rate limits, public exposure, registration. That is the gateway's job, and that is where the SDK helps.

### 2.9.1 `donkey serve` — the listener, in one line

Wrap the official `a2a-sdk`; never reimplement the protocol. Map the framework's native run onto the A2A task lifecycle. Generate the agent card from the same `@donkey.tool` / `@donkey.agent` markers the scanner (2.5) already reads. Bind to localhost by default — the agent is never the public face.

```python
@donkey.agent(name="support-triage", skills=["triage", "draft-reply"])
async def handle(task: A2ATask) -> A2AResult:
    return await graph.ainvoke(task.input)

donkey.serve(handle)      # A2A server on 127.0.0.1:8000, card auto-generated
```

Every call arriving over A2A gets the same treatment as an outgoing call: correlation ID, cost tags, OTel span, and typed refusals when the agent's own downstream calls are blocked. That governance-aware inbound path is what a plain A2A server does not give you.

### 2.9.2 `donkey expose` — the ingress, registered from code

Provision an A2A proxy on Omni Gateway pointing at the agent's URL, attach the policy set, register the card in Agent Registry. The gateway does the work; the SDK turns a console session into one command.

```
$ donkey expose --env prod
  ✓ A2A proxy  https://gw.acme.internal/agents/support-triage
  ✓ policies   token-budget, pii-detection, trusted-agent-identity
  ✓ registry   support-triage v1.4.0
```

### 2.9.3 `donkey dev` — a gateway in front of your laptop

Two honest options, depending on what is available internally:

- **Option A — real gateway.** If a self-managed Omni Gateway image is usable for local dev, `donkey dev` starts it alongside `donkey serve` so the developer hits A2A *through real policies* on their machine. Depends on image availability and licensing — verify internally.
- **Option B — simulated ingress.** If A is blocked, extend the simulator (1.4) with an A2A ingress mode: a local fake gateway in front of `donkey serve` replaying the same rejection fixtures. Fully in our control, same honesty check — a plain A2A client must see byte-identical responses.

Either way the developer experience is one command, and an A2A endpoint that behaves like production.

**Why it matters — Scenario A.** Agent Broker or a partner agent wants to hand the support agent a ticket over A2A. Today that means writing a server, getting a cert, opening a port and registering by hand. With this: decorate, serve, expose — and every inbound task carries the same governance as the outbound calls.

**The gap only the gateway can close.** What would make this feel like magic is an agent with *no inbound reachability at all* — a laptop, a private subnet — that is still publishable: the agent dials *out* to the gateway, and the gateway routes inbound A2A traffic back over that connection, the ngrok / Cloudflare Tunnel pattern. That is not in the public Omni Gateway docs. It is upstream gap #6 (see 3.1), and it is the only version that genuinely removes the listener problem rather than hiding it. Until it ships, the truth is: `donkey serve` makes the listener trivial, `donkey expose` makes the ingress one command, and the agent still has to be somewhere the gateway can reach.

**Effort:** `serve` S–M (the `a2a-sdk` exists) · `expose` L, blocked on verifying the A2A proxy provisioning API · `dev` M.

---

## Phase 2 exit criteria

- At least one framework's official docs link to the SDK.
- Evidence of production use (a customer or internal team running it with OTel attached).
- Refusal handlers, registry, HITL, and scanner all pass conformance against the simulator.

---

# PHASE 3 — Push upstream + platform (6–12 months)

**Goal:** stop papering over gateway gaps; get them fixed in the product, then build the client half of the features that become possible.

---

## 3.1 File the six upstream gaps — **start this in Phase 1, not Phase 3**

These are product requests to the Omni Gateway team. They have long lead times, so file them now even though the client work is Phase 3.

| # | Gap | Ask | What it unblocks |
|---|---|---|---|
| 1 | 429 has no `Retry-After` | Also emit `Retry-After` alongside `x-token-reset` | Stock clients stop mis-retrying without any SDK |
| 2 | No budget-query endpoint | `GET …/budget` returning limit/remaining/reset | 1.3 becomes live instead of stale-in-band |
| 3 | No policy-discovery endpoint | Advertise in-force policies (advisory) | 3.2 |
| 4 | No dry-run / simulation mode | A header or mode that evaluates policy and reports the verdict without blocking | Test against *real* policy config instead of fixtures |
| 5 | Guardrail-verdict error contract under-documented | One documented status/body for all guardrail providers | 1.2's generic fall-through becomes typed |
| 6 | No outbound-connect / tunnel for A2A ingress | Agent dials out; gateway routes inbound A2A back over that connection | 2.9: publish agents with no inbound reachability |

**Team framing.** Gaps 1–5 each make the SDK *smaller*; gap 6 is the one that would make A2A publishing feel like magic. That is the correct direction: a gateway that behaves well by default is a better product than one that needs a client library to be safe.

---

## 3.2 Policy handshake client (after gap #3 ships)

**What it is.** On first connection, the SDK fetches the in-force policy set and exposes it:

```python
donkey.policies.models_allowed        # ["gpt-4o", "claude-sonnet"]
donkey.policies.tools_allowed         # [...]
donkey.policies.budget                # same object as 1.3, now live
donkey.policies.pii.mode              # "block" | "mask" | "log"
donkey.policies.content_safety.on     # True
```

**Why it matters — Scenario A.** The agent asks for a model that is not allowed. Today: one wasted call, one refusal. With the handshake: the adapter picks from `models_allowed` at construction time and the refusal never happens. Multiply by 2,000 tickets a day.

**Why it matters — Scenario C.** `pii.mode == "mask"` tells the bot the gateway will mask rather than block, so the bot's UX can say "some details were redacted" instead of "request failed."

**The hard rule, again.** The handshake is **advisory**. It exists to avoid *wasted* calls and to improve UX. The gateway still evaluates every request. If the client's cached view and the gateway disagree, the gateway wins and the client learns from the refusal. Any proposal to skip the gateway "because the handshake said it's fine" is the client-side enforcement anti-pattern. `donkey.policies.observed_at` is exposed for the same reason `budget.observed_at` is.

**Effort:** L (blocked on upstream).

---

## 3.3 To-the-code push (config / policy / tool-list updates)

**What it is.** The gateway (or control plane) pushes changes — new allow-list, budget change, tool added — and the SDK refreshes `donkey.policies` and `donkey.tools` without a restart. Mechanism: long-poll or SSE against the discovery endpoint from 3.2, with an ETag.

**Why it matters — Scenario A.** A new model is approved at 10:00. Today every agent restarts to pick it up (or nobody tells them). With push, the agent's next run uses it.

**Honest note.** Consistency is the risk: an agent mid-run sees the allow-list change under it. Rule: refresh between runs (`donkey.run()` boundaries), never mid-run.

**Effort:** L (depends on 3.2).

---

## 3.4 Structured output + evaluation hooks

*Structured output.* `client.chat.completions.parse(response_format=MyPydanticModel)` already exists in the `openai` client; the SDK's job is only to make sure refusals and budget parsing still work on the `.parse` path, and that the simulator supports it. Small.

*Evaluation hooks.* A pluggable `donkey.evaluate(on="run_end", with=my_scorer)` that attaches a score to the run's span. Pairs with 1.6; the eval logic itself lives in the customer's tool (Langfuse, Phoenix, Braintrust). Do not build an eval framework.

**Effort:** M combined.

---

## 3.5 TypeScript SDK — only after Python product-market fit

**What it is.** Port of the six-piece minimum, targeting the Vercel AI SDK / Mastra / OpenAI Agents JS audience.

**Gate.** Do not start until Phase 1 exit criteria are met *and* Phase 2 has production evidence. Doubling the maintenance surface before the Python contract has stabilised means fixing every gateway change twice.

**Effort:** XL.

---

## Phase 3 exit criteria / triggers

- At least two of the six upstream gaps shipped in the gateway.
- Policy handshake live against a real endpoint (not `Unverified`).
- **Stop trigger:** if MuleSoft ships a first-party consumption SDK, pivot this project to contributing the simulator, conformance suite, and OTel instrumentation into it rather than competing.

---

# One-page summary for the team

| Phase | Feature | Scenario it saves | Ship when |
|---|---|---|---|
| 1 | LLM client (skeleton) | all | now |
| 1 | Typed refusals | A: 403 ≠ auth error; B: never retry 429 | now |
| 1 | Budget object + pacing | B: finishes overnight unattended | now |
| 1 | Local simulator | A: PII branch tested before prod | now (hero demo) |
| 1 | `simulate()` + conformance plugin | A: "your agent retries refusals" found in CI | now |
| 1 | OTel GenAI | A: refusals in your existing dashboard | now |
| 1 | Correlation + cost tags | A: cost per agent; C: audit join | now |
| 1 | LangGraph deep + raw | A | now |
| 1 | Decorators + `donkey doctor` | all | now |
| 1 | Docs + `llms.txt` | adoption | now |
| 2 | Refusal handlers | B: resume in one line | 3–6 mo |
| 2 | Classification registry | C: custom policy → typed error | 3–6 mo |
| 2 | HITL normalisation | A: refund approval | 3–6 mo |
| 2 | Identity helpers (OBO) | C: per-user policy | 3–6 mo |
| 2 | Scanner + GitHub Action | A: registry from code | 3–6 mo |
| 2 | Kill-switch awareness | C: stops cleanly | 3–6 mo |
| 2 | MCP tool discovery | C: allowed tools only | 3–6 mo |
| 2 | A2A: `serve` · `expose` · `dev` | A: callable by other agents, one command | 3–6 mo |
| 3 | Six upstream gaps | all — *file in Phase 1* | 6–12 mo |
| 3 | Policy handshake | A: no wasted refusals | after gap #3 |
| 3 | To-the-code push | A: new model without restart | after 3.2 |
| 3 | Structured output + eval hooks | A | 6–12 mo |
| 3 | TypeScript SDK | new audience | after PMF |

**Do not build, at any phase:** client-side policy enforcement · client-side semantic caching · a provisioning control plane competing with API Manager / Terraform · re-implementations of Agent Scanners, Kill Switch, or Trusted Agent Identity · an approval UI or queue · an eval framework · the gateway inside the agent process · our own A2A protocol implementation.

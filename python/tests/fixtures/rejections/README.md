# Rejection contracts — the eight documented shapes (#181, #289, docs/verified-apis.md §4)

The canonical index of the gateway rejection shapes `core/errors.classify()`
discriminates, one row per shape. These fixtures are **shared with the local
gateway simulator** (`donkey mock`, #187): the simulator replays these exact
files, so contract drift fails `classify()` and the simulator at once. Keep them
byte-faithful and parser-compatible (see `../anypoint/llm_proxy/README.md` for
the `.headers.txt` / `.body.json` / `.body.empty` convention).

## Provenance & verification status (verification discipline)

These contracts are now **public in the Omni Gateway policy reference**, so each
row below cites its documented source (docs URL + the product line that
introduced the policy) — no longer left open as "no URL recorded" (#253):

| Policy (LLM lane) | Docs page | Introduced |
|---|---|---|
| Injection Protection (row 3) | https://docs.mulesoft.com/gateway/latest/policies-included-injection-protection | v1.12.0 |
| Regex Prompt Guard (row 7) | https://docs.mulesoft.com/gateway/latest/policies-included-regex-prompt-guard | v1.11.4 |
| LLM PII Detection (row 2) | https://docs.mulesoft.com/gateway/latest/policies-included-llm-pii-detection | v1.13.0 |
| LLM Token Rate Limit (row 1) | https://docs.mulesoft.com/gateway/latest/policies-included-llm-token-rate-limit | v1.11.0 |
| Azure Content Safety (row 8) | https://docs.mulesoft.com/gateway/latest/policies-included-azure-content-safety | v1.13.0 |
| Amazon Bedrock Guardrails (row 8, sibling) | https://docs.mulesoft.com/gateway/latest/policies-included-bedrock-guardrails | v1.13.0 |

**Live-capture status (#253, 2026-09-22):** rows 7 and 8 were re-confirmed
against the deployed provisioning proxies — `ddk-injection-guard` (Regex Prompt
Guard, instance 21179713, shared-omni-gateway) and `ddk-azure-content-safety`
(Azure Content Safety, instance 21180957, private-space-omni-gateway). Row 3
(Injection Protection, `x-injection-protection: blocked`) and row 4 (the
fall-through) stay documented-only: **no Injection Protection policy proxy is
deployed** to capture them, so their bytes remain honest placeholders (the
deployed injection-lane proxy runs Regex Prompt Guard = row 7, a distinct
policy). Rows 1/2/5 remain LIVE from the original 2026-08-28 captures.

**Bedrock Guardrails sibling (#568, 2026-09-24):** row 8's second vendor was
live-captured against `ddk-bedrock-guardrails` — a `403` carrying
`x-llm-proxy-bedrock-guardrail-action: reject` /
`x-llm-proxy-bedrock-guardrail-reason: content_filter` and a
`{"error":…,"categories":["content_filter"]}` body. Its bytes live beside the
Azure capture as `reject.content-safety-bedrock.{headers.txt,body.json}` and are
asserted by `test_row8_bedrock_guardrails_variant_is_content_safety_blocked`.
This sibling is a **classify()-contract fixture only** — it is *not* wired into
`simulator.fixtures.SHAPES` (row 8 stays one canonical shape, keyed on the
vendor `…-action: reject` header family), so, unlike every served fixture, it is
deliberately **absent from `fixtures.lock`**; the contract test's assertions
guard its bytes instead.

## The eight rows

| # | Shape | Fixture | classify() → | Discriminator | Provenance |
|---|-------|---------|--------------|---------------|------------|
| 1 | Token rate limit | `../anypoint/llm_proxy/reject.token-rate-limit.{headers.txt,body.empty}` | `TokenBudgetExceeded` | 429 + `x-token-limit`/`-remaining`/`-reset`, empty body | LIVE, 2026-08-28 (`llm-token-rate-limit` 1.0.2) |
| 2 | PII detection | `../anypoint/llm_proxy/reject.pii-detected.{headers.txt,body.json}` | `PIIDetected` | nested `error.type == "pii_detected"`, no `www-authenticate` | LIVE, 2026-08-28 (`llm-pii-detection-policy` 1.0.0) |
| 3 | Injection protection | `reject.injection-protection.{headers.txt,body.empty}` | `PromptInjectionBlocked` | header `x-injection-protection: blocked` (not status) | **Shape UNVERIFIED** — header discriminator only; body unknown → empty placeholder. [Injection Protection policy](https://docs.mulesoft.com/gateway/latest/policies-included-injection-protection) (v1.12.0); still open on #253 — no Injection Protection proxy deployed. |
| 4 | Content moderation / federated guardrails | `reject.content-moderation.{headers.txt,body.empty}` | generic `PolicyViolation` | falls through (no nested error, no injection/guard/safety discriminator) | **UNDER-DOCUMENTED** — minimal 4xx proving the fall-through stays generic. Blocked on #253. |
| 5 | Upstream provider 4xx | `../anypoint/llm_proxy/reject.model-not-found.body.json` | `UpstreamRequestError` | non-429 4xx, nested `error` with `code`/`type`/`param` | LIVE, 2026-08-28 (OpenAI passthrough) |
| 6 | Upstream 5xx | `reject.upstream-5xx.{headers.txt,body.empty}` | `UpstreamModelError` (retryable) | 5xx status range (no competing discriminator) | **SYNTHETIC** — status-range classification only; no live capture, no invented body. |
| 7 | Regex Prompt Guard | `reject.regex-prompt-guard.{headers.txt,body.json}` | `PromptInjectionBlocked` (`policy="regex-prompt-guard"`) | 403 + top-level `matched_patterns` list (flat-string `error`) | **VERIFIED (LIVE), 2026-09-22 (#253)** — body matches the live capture byte-for-byte against `ddk-injection-guard` (instance 21179713). [Regex Prompt Guard policy](https://docs.mulesoft.com/gateway/latest/policies-included-regex-prompt-guard) (v1.11.4). |
| 8 | Content safety / guardrails | `reject.content-safety.{headers.txt,body.json}` (Azure); `reject.content-safety-bedrock.{headers.txt,body.json}` (Bedrock sibling) | `ContentSafetyBlocked` (parses `categories`) | 403 + `x-llm-proxy-<vendor>-…-action: reject` (Azure Content Safety / Bedrock Guardrails) | **VERIFIED (LIVE)** — Azure 2026-09-22 (#253): discriminator headers + body shape confirmed against `ddk-azure-content-safety` (instance 21180957), `.body.json`/`-reason` hold a live-captured set (`severity_hate,severity_violence`; categories are prompt-dependent). Bedrock Guardrails 2026-09-24 (#568): same `…-action: reject` family confirmed against `ddk-bedrock-guardrails` (`content_filter`). [Azure Content Safety policy](https://docs.mulesoft.com/gateway/latest/policies-included-azure-content-safety) (v1.13.0); [Amazon Bedrock Guardrails policy](https://docs.mulesoft.com/gateway/latest/policies-included-bedrock-guardrails) (v1.13.0). |

Rows 1, 2, 5 **alias** the existing live captures in `../anypoint/llm_proxy/`
(referenced, not copied — moving them would break that directory's contract-test
helpers and provenance chain). Rows 3, 4, 6 live here because they are not (yet)
live-captured; their bodies are zero-byte `.body.empty` placeholders — the honest
"shape unknown" marker, never a guessed body.

Rows 7 and 8 (#289) also live here and are now **VERIFIED (LIVE)**: their
discriminators (the `matched_patterns` list, the vendor `…-action: reject`
header) were confirmed against the deployed provisioning proxies — Regex Prompt
Guard and Azure Content Safety on 2026-09-22 (#253), Bedrock Guardrails on
2026-09-24 (#568) — not just pinned from the policy pages. The regex-prompt-guard
body matched the committed bytes exactly; the content-safety fixtures carry a
live-captured category set for each vendor. These shapes have **no `_verify.py` constant** to
flip — `classify()` reads them straight from the response, so the verification
record is the docs/verified-apis.md §4 rows plus this table, not an
`Unverified(...)` guard. Unlike rows 3/4/6, row 7 needs a non-empty body: its
discriminator is a body field (`matched_patterns`), so an empty placeholder could
not exercise the path.

Client-ID enforcement (401 + `www-authenticate` → `AuthError`) is a separate
**consumer-auth** case, deliberately **not** one of the eight policy-rejection
rows.

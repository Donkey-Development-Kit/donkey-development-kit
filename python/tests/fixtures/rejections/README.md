# Rejection contracts — the eight documented shapes (#181, #289, docs/verified-apis.md §4)

The canonical index of the gateway rejection shapes `core/errors.classify()`
discriminates, one row per shape. These fixtures are **shared with the local
gateway simulator** (`donkey mock`, #187): the simulator replays these exact
files, so contract drift fails `classify()` and the simulator at once. Keep them
byte-faithful and parser-compatible (see `../anypoint/llm_proxy/README.md` for
the `.headers.txt` / `.body.json` / `.body.empty` convention).

## Provenance & verification status (verification discipline)

The build guide describes these contracts as "public in Omni Gateway
v1.11–v1.13", but **no public docs URL for the rejection contracts is recorded
anywhere in this repo**, and `v1.11–v1.13` has no concrete in-repo source (the
only live `v1.13` is the Flex Gateway *runtime* v1.13.2; docs/verified-apis.md §4 cites policy
*asset* versions). Writing a docs URL or a "docs version" into these fixtures
would be inventing an endpoint, which the verification discipline forbids. So each row below records
only what is **defensible**, and the "cite the public docs URL + version"
portion of the #181 acceptance criteria is left open under **#253** (`verify:
re-confirm the v1.11-v1.13 rejection contracts against current docs and a
sandbox`) rather than fabricated to green the checkbox.

## The eight rows

| # | Shape | Fixture | classify() → | Discriminator | Provenance |
|---|-------|---------|--------------|---------------|------------|
| 1 | Token rate limit | `../anypoint/llm_proxy/reject.token-rate-limit.{headers.txt,body.empty}` | `TokenBudgetExceeded` | 429 + `x-token-limit`/`-remaining`/`-reset`, empty body | LIVE, 2026-08-28 (`llm-token-rate-limit` 1.0.2) |
| 2 | PII detection | `../anypoint/llm_proxy/reject.pii-detected.{headers.txt,body.json}` | `PIIDetected` | nested `error.type == "pii_detected"`, no `www-authenticate` | LIVE, 2026-08-28 (`llm-pii-detection-policy` 1.0.0) |
| 3 | Injection protection | `reject.injection-protection.{headers.txt,body.empty}` | `PromptInjectionBlocked` | header `x-injection-protection: blocked` (not status) | **Shape UNVERIFIED** — header discriminator only; body unknown → empty placeholder. Blocked on #253. |
| 4 | Content moderation / federated guardrails | `reject.content-moderation.{headers.txt,body.empty}` | generic `PolicyViolation` | falls through (no nested error, no injection/guard/safety discriminator) | **UNDER-DOCUMENTED** — minimal 4xx proving the fall-through stays generic. Blocked on #253. |
| 5 | Upstream provider 4xx | `../anypoint/llm_proxy/reject.model-not-found.body.json` | `UpstreamRequestError` | non-429 4xx, nested `error` with `code`/`type`/`param` | LIVE, 2026-08-28 (OpenAI passthrough) |
| 6 | Upstream 5xx | `reject.upstream-5xx.{headers.txt,body.empty}` | `UpstreamModelError` (retryable) | 5xx status range (no competing discriminator) | **SYNTHETIC** — status-range classification only; no live capture, no invented body. |
| 7 | Regex Prompt Guard | `reject.regex-prompt-guard.{headers.txt,body.json}` | `PromptInjectionBlocked` (`policy="regex-prompt-guard"`) | 403 + top-level `matched_patterns` list (flat-string `error`) | **DOCUMENTED, pending live capture (#253)** — body carries a representative `matched_patterns` list because the discriminator *is* a body field; pinned from the Regex Prompt Guard policy page, not a live capture. |
| 8 | Content safety / guardrails | `reject.content-safety.{headers.txt,body.json}` | `ContentSafetyBlocked` (parses `categories`) | 403 + `x-llm-proxy-<vendor>-…-action: reject` (Azure Content Safety / Bedrock Guardrails) | **DOCUMENTED, pending live capture (#253)** — header discriminator pinned from the Azure Content Safety / Bedrock Guardrails policy pages; the `.body.json` is illustrative, not a live capture. |

Rows 1, 2, 5 **alias** the existing live captures in `../anypoint/llm_proxy/`
(referenced, not copied — moving them would break that directory's contract-test
helpers and provenance chain). Rows 3, 4, 6 live here because they are not (yet)
live-captured; their bodies are zero-byte `.body.empty` placeholders — the honest
"shape unknown" marker, never a guessed body.

Rows 7 and 8 (#289) also live here and are **DOCUMENTED, pending live capture
(#253)**: their discriminators (the `matched_patterns` list, the vendor
`…-action: reject` header) are pinned from the policy pages, and `classify()`
types them from those discriminators, but they have **not** been confirmed
against a live sandbox — no `_verify.py` row flips to `verified=True` for them.
Unlike rows 3/4/6, row 7 needs a non-empty body: its discriminator is a body
field (`matched_patterns`), so an empty placeholder could not exercise the path.
The bodies are the minimal representative shapes needed to drive the
discriminator, explicitly not presented as live captures.

Client-ID enforcement (401 + `www-authenticate` → `AuthError`) is a separate
**consumer-auth** case, deliberately **not** one of the eight policy-rejection
rows.

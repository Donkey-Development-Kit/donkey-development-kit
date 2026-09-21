# M1 — Model access: completion checklist (→ 0.1.0)

Tracks the work between today's state and a **truthful, publishable 0.1.0**
(build plan Phase 1 "M1 — Model access"). M1 ships Pillar 1 only — governed model
access through the Omni Gateway LLM proxy, plus the four Tier-1 adapters, the
conformance kit, the `lint` command, and a docs skeleton.

> **Rule that governs every unchecked box (verification discipline):** never invent an endpoint,
> header, or class name. A box flips to ✅ only when the fact is confirmed
> against the *installed framework* / *real sandbox* — not when the code that
> assumes it is written.

Status keys: ✅ done · 🟡 in progress / partial · ⬜ not started · 🔒 blocked on
external verification (cannot be closed by writing code).

---

## 1. The critical path — verify the 8 constructor signatures (docs/verified-apis.md §8)

**This is the only thing between us and a truthful M1 for the code that already
exists.** Every adapter is wired to the *live-verified proxy contract* (the framework-free core), but
`docs/verified-apis.md` §8 currently lists **all eight** framework
constructor/kwarg signatures as `UNVERIFIED`. The adapters' inline
`# VERIFY …: docs/verified-apis.md §8` markers identify facts that still need
verification; they are **not** proof.

### How to verify (the procedure, made executable)

Use the harness `python/scripts/verify_frameworks.py`. It runs two independent
checks per framework and never guesses a name:

- **A — Signature (offline; needs only the framework `pip`-installed).**
  It calls the adapter's factory, which imports the exact class we name and
  constructs it with the exact kwargs we pass. A wrong class path →
  `ImportError`/`AttributeError`; a wrong kwarg → `TypeError`. It then imports
  the class from its **recorded docs/verified-apis.md §8 path** and checks the object `isinstance` of
  it, so a silently-renamed or re-exported class is caught. **Construction
  succeeding is the signature verification.**
- **B — Live round-trip (`--live`; needs the 3 `DONKEY_LLM_PROXY_*` env vars).**
  Makes one real completion. Only LangGraph's runtime call (`ChatOpenAI.ainvoke`)
  is exercised directly — the other seven frameworks' agent-loop APIs are
  themselves unverified, so the harness constructs the object and relies on the
  already-live-verified shared proxy path (the framework-free core) rather than guessing a method.

```bash
cd python
pip install -e ".[dev,llm,langgraph]"          # install the frameworks you're verifying
python scripts/verify_frameworks.py             # signature check, all installed
python scripts/verify_frameworks.py --live      # + one real proxy round-trip
python scripts/verify_frameworks.py --emit-verified   # prints docs/verified-apis.md §8 markdown rows to paste
```

Per framework, "verified" means: **install the real package → harness green
(signature + live) → maintainer signs off on scope → flip the docs/verified-apis.md §8 row and paste
the confirmed class path → remove/relax the code's UNVERIFIED note.**

### Per-framework signature status

| Framework | Tier | docs/verified-apis.md §8 class we target | Signature (harness) | Live round-trip | docs/verified-apis.md §8 row flipped |
|---|---|---|---|---|---|
| LangGraph | 1 | `langchain_openai.ChatOpenAI` | ✅ confirmed by harness (installed locally; public path re-export verified) | 🟡 needs a `--live` run against the sandbox | ⬜ pending maintainer sign-off |
| Google ADK | 1 | `google.adk.models.lite_llm.LiteLlm` | ⬜ install `[adk]` + run harness | ⬜ | ⬜ |
| Strands | 1 | `strands.models.openai.OpenAIModel` | ⬜ install `[strands]` + run harness | ⬜ | ⬜ |
| MS Agent Framework | 1 | `agent_framework.openai.OpenAIChatClient` | 🔒 adapter raises `blocked on verification` — class path/kwarg unconfirmed; package renamed classes recently | ⬜ | ⬜ |
| OpenAI Agents SDK | 1 | `agents.OpenAIChatCompletionsModel` | ⬜ install `[openai-agents]` + run harness | ⬜ | ⬜ |
| Anthropic SDK | 1 | `anthropic.AsyncAnthropic` | ⬜ install `[anthropic]` + run harness | ⬜ | ⬜ |
| CrewAI | 1 | `crewai.LLM` | ⬜ install `[crewai]` + run harness | ⬜ | ⬜ |
| LlamaIndex | 2 | `llama_index.llms.openai_like.OpenAILike` | ⬜ install `[llamaindex]` + run harness | ⬜ | ⬜ |

> M1's stated scope is the **four Tier-1** adapters (LangGraph, ADK, Strands,
> Agent Framework). OpenAI Agents SDK/Anthropic SDK/CrewAI/LlamaIndex exist but
> land officially in M2; they can be verified opportunistically.

---

## 2. Definition of done, per adapter

Applies to each Tier-1 adapter before it counts as shipped.

| DoD item | LangGraph | ADK | Strands | Agent Framework |
|---|---|---|---|---|
| 1. Passes the conformance kit, or has an asserted documented exemption | ⬜ | 🟡 (correlation-id exemption recorded in `KNOWN_LIMITATIONS`) | ⬜ | ⬜ |
| 2. Runnable `examples/<framework>/` (env-vars-only) | ✅ | ✅ | ✅ | ✅ (guards the blocked path) |
| 3. Docs page with the manual equivalent (users can eject) | ✅ (`website/`) | ✅ | ✅ | ✅ |
| 4. Listed in the nightly matrix | ✅ (`.github/workflows/nightly-matrix.yml`) | ✅ | ✅ | ✅ |
| 5. Version floor declared, no ceiling | ✅ (pyproject extras) | ✅ | ✅ | ✅ |

> DoD #1 is the real gap: the conformance **scenario bodies** are not yet wired
> (`tests/conformance/suite.py` fixes the scenario list + exemption table only).
> Wiring them against the captured contract fixtures (BG §1.5) is the largest
> remaining M1 build task after signature verification.

---

## 3. Remaining M1 build tasks (code, once Section 1 is unblocked)

- ⬜ **Conformance kit bodies** — implement the M1-relevant scenarios
  (`simple_completion`, `streaming_completion`, `attribution_headers_present`,
  `correlation_id_propagated`, `policy_violation_terminal`) against captured
  fixtures + the shared proxy path, executed identically per adapter.
- 🟡 **`lint` command** — provisioning `lint` exists; confirm it's exposed and
  documented for the M1 surface, or descope to M2 explicitly.
- ✅ **Framework-free client** (`donkey.llm.client()`) — live-verified.
- ✅ **Error taxonomy + `classify()`** — live-verified against the 4 rejection shapes.
- ✅ **Model handles** (`resolve()`), honest `list_models(live=True)` ConfigError.
- ✅ **Two adapter ergonomics** — `connection_kwargs()` + module-level factories.
- ✅ **Docs site skeleton** (`website/`, Nextra) — deployable to Vercel/Railway.
- ⬜ **`openai` major-version decision** — extras have no ceiling, so a fresh
  install pulls openai 3.x, which breaks the httpx transport injection the SDK
  is verified against (openai 1.x). Decide: cap `openai<2` (documented exception
  to "floors, never ceilings") **or** verify against openai 3.x / httpx2. See the
  `openai-1x-http-client-constraint` memory.

---

## 4. Gate status (must stay green)

- ✅ `ruff check .`
- ✅ `mypy` (`--strict`, incl. `scripts/verify_frameworks.py`)
- ✅ `lint-imports` (2 contracts: core-is-framework-free, layered architecture)
- ✅ `pytest` (unit + ergonomics)
- ⬜ nightly framework matrix green on at least the Tier-1 four (needs their
  extras installable in CI)

---

## 5. Publish-readiness (the trademark/support boundary / docs/unsupported-boundary.md)

- ⬜ Resolve the **maintainer / support statement** TODO in the README (the trademark/support boundary) —
  blocks any public release; decide the distribution name if not MuleSoft-endorsed.
- ✅ `docs/unsupported-boundary.md` exists and is linked from the README.
- ⬜ Tag `0.1.0` only after the Tier-1 four are signature-verified **and** their
  conformance bodies pass (or carry an asserted exemption).

---

_Definition of "M1 done": the four Tier-1 adapters are signature-verified against
installed packages (Section 1), pass the wired conformance scenarios or carry an
asserted exemption (Section 2), all gates + the nightly matrix are green (Section 4), and the
trademark/support boundary support statement is resolved (Section 5). Everything else in the plan (Pillars 2–3,
Publication, TypeScript) is out of M1 scope._

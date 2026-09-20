# Scenarios

The feature pages tell you what each piece *is*. These pages show three of them
working together on a real job, start to finish — the same three scenarios the
build guide uses to justify the skeleton (`BG §1.8`). Each one is runnable
today against the [local simulator](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md), with no Anypoint credentials
and no real gateway, so you can watch the governance branch execute in your own
terminal before it ever runs on a customer's data.

| Scenario | The job | What it exercises |
| --- | --- | --- |
| [Support triage](https://donkey-development-kit.github.io/donkey-development-kit/scenarios/support-triage.md) | Draft replies to a queue of support tickets; one carries PII | Typed refusals ([`PIIDetected`](https://donkey-development-kit.github.io/donkey-development-kit/errors.md)), [correlation IDs](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md), [OTel spans](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md) — the shipped LangGraph demo, end to end |
| [Nightly batch](https://donkey-development-kit.github.io/donkey-development-kit/scenarios/nightly-batch.md) | Enrich 50,000 records overnight against a windowed budget, unattended | [Budget pacing](https://donkey-development-kit.github.io/donkey-development-kit/budget.md) (`pace()` / `wait_for_reset()`) and resume, driven by the [simulator's `budget` scenario](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md#scenario-scripting) |
| [Internal copilot](https://donkey-development-kit.github.io/donkey-development-kit/scenarios/internal-copilot.md) | An internal assistant whose output must clear a content-safety guardrail | [`ContentSafetyBlocked`](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) via the `donkey-sim/content-safety` sentinel, plus per-run correlation |

  **"Scenario" means two different things in these docs — don't conflate them.**
  These pages are the three *product* scenarios (a job you'd actually run). The
  [simulator's `--scenario` flag](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md#scenario-scripting) names three
  *failure-injection rules* (`pii_block`, `budget`, `injection`). The pages
  below use those rules as the engine, but the job is the story.

## What's runnable, and what's honest about being blocked

Support triage runs **completely** today — it is the Phase-1 acceptance
artefact (`BG §1.8`), timed in CI so its first-run experience can't rot. The
nightly-batch and internal-copilot walkthroughs run their **governed-call and
refusal-handling** paths against the simulator now; where a step depends on a
surface that is still [blocked on verification](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md) — MCP
tool access, agent identity, the kill switch — the page says so in the reader's
terms and shows the shape without inventing an endpoint (§0.3). A "blocked"
note here means *known, deliberate, and tracked*, never *guessed*.

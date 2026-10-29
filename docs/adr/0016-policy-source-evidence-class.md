# ADR 0016: A gateway response shape read from the policy's own source may be typed, at `VERIFIED (POLICY SOURCE)`

- **Status:** Accepted
- **Date:** 2026-10-29
- **Issue:** #1429 (the live capture it does not replace is #1184; the shape was
  first typed in #1176 and untyped in #1451)

## Context

The verification discipline (`§0.3`, build plan "Verification discipline";
`ARCHITECTURE.md` "Verification discipline") says the SDK never invents an
endpoint, header name or class name. For `classify()` (`core/errors.py`) that
has meant a rejection shape is typed only from a capture off a live governed
proxy, recorded in [`docs/verified-apis.md`](../verified-apis.md) §4 at
`VERIFIED (LIVE)` and kept as a fixture under
`python/src/donkey_kit/simulator/_fixtures/rejections/`. Every other refusal
falls through to the generic `PolicyViolation`.

The CrowdStrike AIDR Guardrails policy (`crowdstrike-aidr-guardrails-policy`
1.0.0) can't be captured that way for the foreseeable future: a proxy that runs
it needs a CrowdStrike AIDR for Agents subscription (#1176), so #1184 has no
date. #1176 typed its block as `ContentSafetyBlocked(provider="crowdstrike-aidr")`
from the policy's source code, which names the `x-llm-proxy-crowdstrike-aidr-*`
headers and the body, and from the public policy page, which names the status
codes. The ledger row read `UNVERIFIED`, and the code and docs said AIDR was
"identified specifically". That overclaimed, and #1451 removed the typing, so an
AIDR block became a `PolicyViolation` that names no provider.

Reading a policy's source is not guessing. The headers and body come from the
code the gateway runs, at a version anyone can check. It is still weaker than a
capture: a deployment can differ from the source we read, and nothing proves the
gateway serves those bytes. The ledger had no class for that evidence, so it had
to be labelled either `UNVERIFIED`, which hides how much we know, or
`VERIFIED (LIVE)`, which is false.

## Decision

1. **A new evidence class, `VERIFIED (POLICY SOURCE)`.** It is a gateway
   response shape (status, headers, body) read from the gateway policy's own
   source code, at a named policy asset and version or commit, and not observed
   from a live response. Its definition lives in the legend of
   `docs/verified-apis.md`.
2. **`classify()` may type a shape at this class only when all three hold:**
   1. the shape's ledger row names, in its **Source** column, the policy source
      it was read from: the policy asset and its version or commit;
   2. its fixture is labelled, in its directory's README, as derived from that
      source and not captured;
   3. the typed exception's remediation, and every docs page that describes the
      type, say the shape is read from the policy source and not captured live.

   A shape that misses any of the three falls through to the generic
   `PolicyViolation`, like any other unconfirmed refusal. This supersedes the
   rule, applied in #1451, that an unconfirmed content-safety shape must fall
   through: it still applies to shapes below this class. Review enforces the
   three conditions; `test_a_policy_source_shape_states_its_evidence`
   (`tests/unit/test_errors.py`) holds condition 3 for CrowdStrike AIDR.
3. **The class types a response shape, and nothing else.** It never removes a
   `_verify.blocked(...)` guard, never flips an `Unverified(...)` value to a
   plain constant, and never verifies an endpoint, a request header or a class
   name. Those still need a live sandbox.
4. **The live-capture issue stays open.** A policy-source shape keeps its
   capture issue open until a capture replaces the derived fixture and the row
   moves to `VERIFIED (LIVE)`. If the capture differs, the capture wins.
5. **Code never carries the label.** As for every status,
   `scripts/check_verification_claims.py` rejects `VERIFIED (POLICY SOURCE)` and
   `(POLICY SOURCE, …)` in `src/` outside `core/_verify.py`. Code says what the
   evidence is in words ("read from its policy source, not a live capture") and
   cites the ledger.
6. **The docs never show it as live.** `scripts/generate_docs.py` maps the
   label to its own `policy-source` badge ("Policy-source", neutral tone) and to
   the `offline-verified` page status. It never maps it to `live`.

### Alternatives considered

- **Keep falling through (#1451).** Rejected: an AIDR block reaches the caller
  as a `PolicyViolation` with no provider, although we know which policy sent it
  and from which code. Callers write their own `classify.register()` rule for a
  shape we have read, and #1184 has no date by which that ends.
- **Hold the header names as `Unverified(...)` placeholders.** Rejected: that
  mechanism is for a value the caller can override while it waits on a sandbox.
  A response shape isn't a value the caller sends, and a warning on every read
  would tell the caller nothing it can act on.
- **Reuse `VERIFIED-SHAPE-ONLY`.** Rejected: in the ledger that label means a
  data shape confirmed from an Anypoint-adjacent source (A2D), not the direct
  contract, and its rows validate SDK value types only, with no code path wired
  to them. Typing a refusal from it would give every row at that label a power
  it was never meant to have, and it doesn't say which policy and version the
  shape came from.
- **Label the row `VERIFIED (LIVE)` with a footnote.** Rejected: the badge is
  what most readers see, and it would claim an observation that never happened.

## Consequences

- CrowdStrike AIDR is typed again: a `403` with
  `x-llm-proxy-crowdstrike-aidr-action: reject` is
  `ContentSafetyBlocked(provider="crowdstrike-aidr")`, and the fail-closed `503`
  is `UpstreamModelError`. The `_wire.py` header constants and the
  `_CONTENT_SAFETY_VENDORS` row in `core/_refusal_body.py` are back, with
  wording that states the evidence and doesn't say "identified specifically".
- A policy-source shape is a built-in shape for ADR 0012:
  `classify.register()` refuses a registration that the built-in rule shadows,
  as it did before #1451.
- The derived AIDR fixture stays unserved and out of `fixtures.lock`, which
  lists only the files the simulator serves; the README in its directory
  labels it as derived.
- `docs/verified-apis.md` has the class in its legend, the §8.4 status mapping
  and the AIDR rows of §4. The build plan's `§0.3` section and
  `ARCHITECTURE.md` "Verification discipline" point here.
- The website's errors, telemetry, API reference and unsupported-boundary pages
  name AIDR and say its shape is read from the policy source.
- Reviewers have one more class to check. A PR that types a new shape at this
  class must show all three conditions in rule 2.

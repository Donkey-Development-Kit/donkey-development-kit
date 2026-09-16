# CLI & decorators

  **Phase 1 — mostly designed, not yet shipped.** One command has landed: the
  local simulator, run as `donkey mock` (see [Local simulator](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md)).
  The remaining commands and the decorator names below are a proposal; the
  committed part is the behaviour and the acceptance bar. See
  [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md) and [Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md).

Two on-ramps. Everything on the [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md)'s six-piece list attaches
to the same transport hooks, and these are how you reach all of it without
wiring each piece by hand.

## Decorators

```python
@donkey.governed(team="support")
async def handle_ticket(ticket): ...
```

One decorator gives that function a run ID, cost tags, an OTel span, and typed
refusals — the four things you would otherwise set up per call site.

```python
@donkey.tool
async def lookup_crm(customer_id: str) -> dict: ...
```

`@donkey.tool` marks a function as a governed tool. It does nothing on its own
in Phase 1; the [Phase 2 scanner](https://donkey-development-kit.github.io/donkey-development-kit/publishing.md) reads these markers to derive a
manifest from your code, so marking them early costs nothing and saves the
migration later.

## The CLI

```bash
donkey init          # writes .donkey-kit.toml, prints which env vars are missing
donkey doctor        # checks creds, reaches the gateway, reports budget state
donkey mock          # the local simulator — SHIPPED; --scenario scripts failures
donkey test          # the conformance suite
```

### `donkey doctor` is the one that pays for itself

A governed call can fail for several reasons that all look identical from the
outside. `doctor` exists to tell them apart:

- **Wrong credentials** — the `client_id`/`client_secret` pair is rejected.
- **Wrong URL** — the credentials are fine but the base URL is not a proxy
  instance. (The trailing `/v1` mistake lands here; the governed proxy has no
  `/v1` segment.)
- **Credentials fine, model not in the allow-list** — nothing is misconfigured
  at all; your platform team has not granted that model.

Each answer comes with the same remediation string the matching typed
exception carries, so the fix is in the output rather than in a runbook. This
is the difference between an afternoon of guessing and thirty seconds.

## Acceptance bar

- `donkey doctor` distinguishes those three failures from each other — not one
  generic "could not connect."
- Each verdict prints the remediation string from the corresponding typed
  refusal, so the CLI and the exception never disagree.

---

**Status: Phase 1 — `donkey mock` (the local simulator) is shipped;
`init` / `doctor` / `test` and the decorators are designed, not yet shipped.**

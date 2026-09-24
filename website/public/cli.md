# CLI & decorators

Live

Two on-ramps to the SDK: decorators that govern a function in one line, and a
four-command CLI for setup, diagnosis, local simulation, and conformance
testing.

## Decorators

### `@donkey.governed`

Runs a function inside a [`donkey.run()`](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md) scope:

```python
@donkey.governed(team="support")
async def handle_ticket(ticket): ...
```

One decorator gives the function a run/correlation ID, cost tags, an OTel span,
and typed refusals — the four things you would otherwise set up per call site.

- Wraps **both sync and async** callables.
- Works bare (`@donkey.governed`) or parametrised
  (`@donkey.governed(team=…, project=…, env=…, enduser_id=…)`).
- Takes **no `id=`**: each call opens its own run, so unrelated calls are never
  collapsed into one correlation. When you need a specific id, use
  `donkey.run(id=…)` directly.

### `@donkey.tool`

```python
@donkey.tool
async def lookup_crm(customer_id: str) -> dict:
    """Look up a customer record by id."""
    ...
```

Marks a function as a governed tool **without changing how it's called**. It
returns the same function with a `__donkey_tool__` marker and records a
`ToolSpec` (name, qualname, signature, docstring, `is_async`) in a
process-global registry you read with `registered_tools()`. Both `ToolSpec` and
`registered_tools` are exported from `donkey_kit`.

A tool with **no docstring is rejected at decoration time** (`ValueError`) — an
undescribed tool is useless to a model and to a registry.

The same markers are what the planned [scanner](https://donkey-development-kit.github.io/donkey-development-kit/publishing.md) and
[A2A](https://donkey-development-kit.github.io/donkey-development-kit/a2a.md) agent-card generator read, so marking tools now carries forward.

## The CLI

```bash
pip install "donkey-kit[cli]"
```

```bash
donkey init          # writes a commented .donkey-kit.toml, names every missing env var at once
donkey doctor        # checks creds, reaches the gateway, reports budget state
donkey mock          # the local simulator — --scenario scripts failures
donkey test          # a thin front end to pytest --donkey-conformance
```

### Global flags

Three global flags precede the subcommand:

```bash
donkey --config ./cfg.toml init   # write/read a config file at a non-default path
donkey --env Sandbox doctor       # override the Anypoint environment
donkey --json init                # machine-readable output where a command supports it
```

### Exit codes

Every command exits non-zero on failure, so any of them drops into CI as a
preflight. A command that needs an optional extra (`donkey mock` → `[local]`,
`donkey test` → `[test]`, `donkey doctor` → `[llm]`) prints the exact
`pip install` line and exits `1` — never a stack trace.

### `donkey init`

Resolves your current configuration (kwargs → env vars → `.donkey-kit.toml` →
defaults) and writes a **commented** `.donkey-kit.toml` with the values it
found.

- **Names every missing required field at once** — control plane *and* LLM
  proxy — using the same validation the SDK runs at call time, so `init` and a
  real request never disagree about what is required.
- **Never writes a secret.** `client_secret`, `llm_proxy_client_secret`, and
  `llm_proxy_key` are emitted as commented `env` pointers, not values.
- **Idempotent.** An existing file is left untouched unless you pass `--force`.

```bash
donkey init --force
donkey --json init    # {"path": "...", "written": true, "missing": [...]}
```

### `donkey doctor`

A governed call can fail for several reasons that look identical from the
outside. `doctor` makes one real governed call and tells them apart:

- **Wrong credentials** — the `client_id`/`client_secret` pair is rejected.
- **Wrong URL** — the credentials are fine but the base URL is not a proxy
  instance. (A trailing `/v1` lands here; the governed proxy has no `/v1`
  segment.)
- **Credentials fine, model not in the allow-list** — nothing is misconfigured;
  your platform team has not granted that model.

Each verdict prints the same remediation string the matching typed exception
carries, so the fix is in the output rather than in a runbook. The budget line
states how old the reading is, since the proxy has no budget endpoint.

```bash
donkey doctor
```

```text
[ok] config       env (3 fields)
[ok] gateway      reachable, responded
[ok] credentials  client_id accepted
[ok] model        accepted by the proxy
[i]  budget       99,000 / 100,000 remaining, resets in 59s, observed 0s ago
```

```bash
donkey doctor --model gpt-4o     # model to test against the allow-list (default gpt-4o)
donkey doctor --json             # machine-readable checks
```

### `donkey mock`

Runs the [local simulator](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md), which replays captured gateway
rejections. Needs the `[local]` extra.

```bash
donkey mock --port 8080 --host 127.0.0.1 \
  --scenario pii_block:every=5 \
  --scenario budget:limit=20000,window=60s
```

| Flag | Default | Meaning |
|---|---|---|
| `--port` | `8080` | TCP port to bind. |
| `--host` | `127.0.0.1` | Host/interface to bind. |
| `--scenario` | none | Fault-injection rule, repeatable. See [Scenario scripting](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md#scenario-scripting). |

An invalid `--scenario` exits with code `2`.

### `donkey test`

A thin front end to `pytest --donkey-conformance` — it does not re-implement the
runner. Point it at your agent factory and pass any trailing pytest arguments
straight through; pytest's exit code becomes `donkey test`'s own. Needs the
`[test]` extra.

```bash
donkey test --agent my.pkg:make_agent -k governance -x
```

See [Testing & conformance](https://donkey-development-kit.github.io/donkey-development-kit/testing.md) for what the suite checks.

## Planned commands Roadmap

These commands are part of the [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md) and are not available in the
CLI yet:

- `donkey scan` and `donkey publish` — derive a manifest and agent card from
  your code and register them with Exchange. See [Scan & publish](https://donkey-development-kit.github.io/donkey-development-kit/publishing.md).
- `donkey serve`, `donkey expose`, and `donkey dev` — serve your agent over
  A2A and expose it through the gateway. See [A2A agents](https://donkey-development-kit.github.io/donkey-development-kit/a2a.md).

  Run `donkey --help` to see the commands available in your installed version.

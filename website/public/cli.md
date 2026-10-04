# CLI & decorators

Live

Two on-ramps to the SDK: decorators that govern a function in one line, and a
four-command CLI for setup, diagnosis, local simulation, and conformance
testing.

## Decorators

### `@donkey.governed`

Runs a function inside a [`donkey.run()`](https://docs.donkey-kit.dev/telemetry.md) scope:

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

The same markers are what the planned [scanner](https://docs.donkey-kit.dev/publishing.md) and
[A2A](https://docs.donkey-kit.dev/a2a.md) agent-card generator read, so marking tools now carries forward.

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
donkey --config ./cfg.toml init   # write the generated config file to a non-default path
donkey --env Sandbox init         # write this Anypoint environment into the generated file
donkey --json init                # machine-readable output where a command supports it
```

`--config` and `--env` apply to `init` only. Every other command reads its
configuration from environment variables and the working directory's config
files, so it rejects them with exit `2` rather than run against a configuration
you didn't ask for. To point `doctor` at another environment, set
`ANYPOINT_ENV`, or run it from the directory that holds the config file.

### Exit codes

Every command exits non-zero on failure, so any of them drops into CI as a
preflight. A command that needs an optional extra (`donkey mock` → `[local]`,
`donkey test` → `[test]`, `donkey doctor` → `[llm]`) prints the exact
`pip install` line and exits `1` — never a stack trace. A command whose platform
API is not verified yet exits `3` with `blocked on verification`, so a script can
tell "blocked" from "failed".

The CLI lives in the `donkey_kit.cli` package. Earlier releases also shipped
hidden provisioning commands (`validate`, `plan`, `apply`, `drift`, `lint`,
`generate`); they were removed with the refused provisioning control plane, and
typer now rejects them as unknown commands (exit `2`).

### `donkey init`

Resolves your current configuration (env vars → config files → defaults; see
[Precedence](https://docs.donkey-kit.dev/reference/configuration.md#precedence)) and writes a **commented** `.donkey-kit.toml` with the values it
found.

- **Names every missing required field at once** — control plane *and* LLM
  proxy — using the same validation the SDK runs at call time, so `init` and a
  real request never disagree about what is required.
- **Never writes a secret.** `client_secret`, `llm_proxy_client_secret`, and
  `llm_proxy_key` are emitted as commented pointers, not values. Put them in a
  gitignored `.donkey-kit.local.toml` next to the file, or in environment
  variables. A URL in `.donkey-kit.toml`, including a loopback one, only
  receives credentials from those two files unless you opt in; see
  [Which credentials a URL receives](https://docs.donkey-kit.dev/reference/configuration.md#which-credentials-a-url-receives).
  The file's comments also say that URLs must use `https://`, that `localhost`
  is accepted over plain `http://`, and that `DONKEY_ALLOW_HTTP=1` in the
  environment (not in the file) allows plain `http://` to other hosts.
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

Before the call, `doctor` prints each endpoint's host and where it came from:
`env`, `project file`, `local overlay`, `user file` or `default`. If the LLM
proxy URL comes from the working directory's config files but its credentials
don't (a loopback URL such as the simulator's included), the `config` line
fails with the
[remediation](https://docs.donkey-kit.dev/reference/configuration.md#which-credentials-a-url-receives) and no
request is sent. A control-plane endpoint with the same problem shows its
remediation on the `control plane` line without failing the report, since
`doctor` only calls the LLM proxy. In `jwt` mode, an LLM proxy URL from those
files always fails the `config` line, because the JWT never comes from a file:
set the URL in the environment or opt in.

With `DONKEY_ALLOW_HTTP` on in the environment, a `plain http` line says so and
quotes the value as set, for example `DONKEY_ALLOW_HTTP=true` (in `--json`
output, the entry with `"name": "plain http"`):

```text
[i]  plain http     allowed to non-loopback hosts (DONKEY_ALLOW_HTTP=1 in env)
```

```bash
donkey doctor
```

```text
[ok] config         env (3 fields)
[i]  llm endpoint   <ingress-gw> (env)
[i]  control plane  anypoint.mulesoft.com (default)
[ok] gateway        reachable, responded
[ok] credentials    client_id accepted
[ok] model          accepted by the proxy
[i]  budget         99,000 / 100,000 remaining, resets in 59s, observed 0s ago
```

```bash
donkey doctor --model gpt-4o     # model to test against the allow-list (default gpt-4o)
donkey doctor --json             # machine-readable checks
```

### `donkey mock`

Runs the [local simulator](https://docs.donkey-kit.dev/simulator.md), which replays captured gateway
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
| `--scenario` | none | Fault-injection rule, repeatable. See [Scenario scripting](https://docs.donkey-kit.dev/simulator.md#scenario-scripting). |

The port isn't read from an environment variable or `.donkey-kit.toml`; these
flags (or the `serve()` keyword arguments) are the only way to change it. See
[Choosing a port or host](https://docs.donkey-kit.dev/simulator.md#choosing-a-port-or-host) for the
programmatic entry point and the `0.0.0.0` caveat.

An invalid `--scenario` exits with code `2`.

### `donkey test`

A thin front end to `pytest --donkey-conformance` — it does not re-implement the
runner. Point it at your agent factory and pass any trailing pytest arguments
straight through; pytest's exit code becomes `donkey test`'s own. Needs the
`[test]` extra.

```bash
donkey test --agent my.pkg:make_agent -k governance -x
```

See [Testing & conformance](https://docs.donkey-kit.dev/testing.md) for what the suite checks.

## Planned commands Roadmap

These commands are part of the [Roadmap](https://docs.donkey-kit.dev/roadmap.md) and are not available in the
CLI yet:

- `donkey scan` and `donkey publish` — derive a manifest and agent card from
  your code and register them with Exchange. See [Scan & publish](https://docs.donkey-kit.dev/publishing.md).
- `donkey serve`, `donkey expose`, and `donkey dev` — serve your agent over
  A2A and expose it through the gateway. See [A2A agents](https://docs.donkey-kit.dev/a2a.md).

  Run `donkey --help` to see the commands available in your installed version.

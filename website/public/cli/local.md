# Develop locally

Status: Live. Shipped, and verified against a real governed gateway.

## `donkey mock`

Runs the [local simulator](https://docs.donkey-kit.dev/simulator.md), which replays captured gateway
rejections. Needs the `[local]` extra.

```bash
donkey mock --port 8080 --host 127.0.0.1 \
  --scenario pii_block:every=5 \
  --scenario budget:limit=20000,window=60s \
  --scenario request_limit:limit=3,window=60s
```

The flags are listed in the [command reference](https://docs.donkey-kit.dev/cli.md#command-reference). The
`--scenario` rule syntax is in [Scenario scripting](https://docs.donkey-kit.dev/simulator.md#scenario-scripting).

The port isn't read from an environment variable or `.donkey-kit.toml`; these
flags (or the `serve()` keyword arguments) are the only way to change it. See
[Choosing a port or host](https://docs.donkey-kit.dev/simulator.md#choosing-a-port-or-host) for the
programmatic entry point and the `0.0.0.0` caveat.

An invalid `--scenario` exits with code `2`.

## `donkey dev`

Serves a `@donkey.agent` handler over A2A behind a simulated gateway ingress,
which rejects inbound calls with the simulator's fixtures. Needs the `[a2a]`
and `[local]` extras.

```bash
donkey dev my_agent:handle --port 8000 --scenario pii_block:every=5
```

`TARGET` is `module:attribute`, imported from the current directory. The
ingress, the rules it takes and where its rejection shapes come from are in
[Simulated ingress](https://docs.donkey-kit.dev/a2a/serve.md#simulated-ingress). A target that can't be loaded or
isn't marked with `@donkey.agent`, and an invalid `--scenario` (including
`budget`), exit with code `2`.

`--protocol-version` accepts `1.0` (default) or `0.3.0`; an unsupported value
also exits `2` before loading the target. `--agent-version` sets the card's
application release version (default `0.0.0`), not the protocol:

```bash
donkey dev my_agent:handle --protocol-version 0.3.0 --agent-version 2.4.0 \
  --scenario pii_block:every=5
```

v1 supports `injection`, `pii_block` and `request_limit` on `SendMessage` and
`SendStreamingMessage`. v0.3 supports only PII/rate replay on `message/send`;
`injection` exits `2` at startup. With scenarios configured, `message/stream`
gets local plain-text HTTP `400` before a rule advances, even if none would
reject. With no scenarios it passes through. Known wrong-version call methods
and explicit mismatched version headers on calls get the same local `400`,
not an invented gateway refusal. Every response carries
`x-donkey-simulator: true`. See the
[scenario/version/method matrix](https://docs.donkey-kit.dev/simulator/a2a.md#a2a-ingress-replay).

`--host` defaults to `127.0.0.1`. A host that isn't loopback, such as
`0.0.0.0`, still binds, but `donkey dev` prints a warning on stderr. Nothing
authenticates the caller, so anyone who can reach the port can call the agent.
The ingress answers a request body over 4 MiB with a `413`.

## `donkey test`

A thin front end to `pytest --donkey-conformance` — it does not re-implement the
runner. Point it at your agent factory and pass any trailing pytest arguments
straight through; pytest's exit code becomes `donkey test`'s own. Needs the
`[test]` extra.

```bash
donkey test --agent my.pkg:make_agent -k governance -x
donkey test --agent my.pkg:make_agent --donkey-mode server   # over a real port; needs [local]
```

See [Testing & conformance](https://docs.donkey-kit.dev/testing.md) for what the suite checks.

## Next steps

- [Local simulator](https://docs.donkey-kit.dev/simulator.md): the scenarios `donkey mock` scripts.
- [Testing & conformance](https://docs.donkey-kit.dev/testing.md): the offline test helpers `donkey test` runs.
- [Ship](https://docs.donkey-kit.dev/cli/ship.md): the commands that publish what you built here.

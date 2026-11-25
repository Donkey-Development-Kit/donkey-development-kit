# Set up

Status: Live. Shipped, and verified against a real governed gateway.

## `donkey init`

Resolves your current configuration (env vars → config files → defaults; see
[Precedence](https://docs.donkey-kit.dev/reference/configuration.md#precedence)), from the working directory,
and writes a **commented** `.donkey-kit.toml` with the non-secret connection
values it found.

- **Names every missing required field at once** — control plane *and* LLM
  proxy — using the same validation the SDK runs at call time, so `init` and a
  real request never disagree about what is required.
- **Keeps your connection mode.** Besides the IDs and URLs, it writes the
  LLM proxy auth mode (`llm_proxy_auth`, only when you set one), the
  model-wallet selector (`llm_proxy_wallet_client_id`) and the dynamic-key
  header name (`llm_proxy_key_header`), so the generated file can express a
  `jwt`, `bearer` or Dynamic API Key setup. In `jwt` and `bearer` mode the token
  itself comes from an `AuthProvider` you pass in code
  (`Donkey(llm_auth=...)`); it is never a config value, so CLI commands that
  call the proxy can't use those modes.
- **Never writes a secret.** `client_secret`, `llm_proxy_client_secret`, and
  `llm_proxy_key` are emitted as commented pointers, not values. Put them in a
  gitignored `.donkey-kit.local.toml` next to the file, or in environment
  variables. A `base_url` or `llm_proxy_url` that carries a credential (a user
  name or password, or a query parameter such as `api_key` or `token`), or that
  can't be parsed, is refused rather than written, and the message names the
  field and where it was set, never its value.
  A URL in `.donkey-kit.toml`, including a loopback one, only
  receives credentials from those two files unless you opt in; see
  [Which credentials a URL receives](https://docs.donkey-kit.dev/reference/configuration.md#which-credentials-a-url-receives).
  The file's comments also say that URLs must use `https://`, that `localhost`
  is accepted over plain `http://`, and that `DONKEY_ALLOW_HTTP=1` in the
  environment (not in the file) allows plain `http://` to other hosts.
- **Idempotent.** An existing file is left untouched, and `init` exits `0`,
  unless you pass `--force`.
- **`--force` regenerates the whole file.** It is not a merge: tables `init`
  doesn't generate, such as `[donkey.agents]`, `[donkey.publication]` or
  `[donkey.expose]`, are not carried over, and `init` names the ones it dropped
  on stderr. Don't use `--force` on a file holding declarations you wrote by
  hand.

Where it writes: the global `--config` path, else `./.donkey-kit.toml`. Missing
parent directories are created. The file appears complete or not at all, and a
replaced file keeps its permissions. `init` exits `1`, writes nothing and prints
no JSON document when the destination is a symlink (including a dangling
one), a directory, or a file that holds secrets (`.donkey-kit.local.toml`,
`.env`, `.env.*`, `.envrc`, `donkey.lock.local`), and when a file appears at the
destination while `init` is writing. `--force` overrides none of these refusals.
Replacing an existing file is atomic for readers, but if another program saves
the same file at that moment, the last write wins.

```bash
donkey init --force
donkey --json init    # {"path": "...", "written": true, "missing": [...]}
```

## `donkey doctor`

A governed call can fail for several reasons that look identical from the
outside. `doctor` makes one real governed call and tells them apart:

- **Wrong credentials** — the `client_id`/`client_secret` pair is rejected.
- **Wrong URL** — the gateway answers, but no proxy is deployed on that base
  path: an empty `404` with no `x-llm-proxy-*` headers. The `gateway` line
  fails and `credentials` and `model` show as not checked. A typo in the path,
  a trailing `/v1` (the governed proxy has no `/v1` segment), or a proxy that
  is not Active in API Manager all land here.
- **Credentials fine, model not in the allow-list** — nothing is misconfigured;
  your platform team has not granted that model.
- **`/responses` not served on this route** — the proxy accepted the
  credentials and routed the model, but the upstream answered the Responses API
  with `404`. An Azure OpenAI route does this. `doctor` then tries
  `/chat/completions` once and reports the route as working if that answers.
  Call it with `client.chat.completions.create(...)` on `donkey.llm.client()`,
  or with your framework's chat-completions client.

Any other refusal confirms only what it proves, so a refused call never shows
the model as working:

- **Model not routable** — the gateway refused the model before calling any
  upstream. The `model` line fails and `credentials` shows as not confirmed.
- **Upstream failure** — the provider failed the call. The `model` line fails.
- **Policy refusal** — a gateway policy, such as PII detection, refused the
  probe. That is an informational line and `doctor` exits `0`. The credentials
  were accepted, but the model is reported as routed only when the response
  names the provider it was routed to.
- **Anything else** — a `probe` line fails with the error, and neither the
  credentials nor the model are confirmed.

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
donkey --config ./cfg.toml doctor  # read cfg.toml and its adjacent local overlay
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

On an Azure OpenAI route, the `responses` line explains the `404` and the run
still exits `0`:

```text
[ok] gateway           reachable, responded
[ok] credentials       client_id accepted
[ok] model             accepted by the proxy
[i]  responses         /responses not served on this route (404 from the azureopenai upstream)
     remediation: This route's upstream does not serve the OpenAI Responses API. Call it with the Chat Completions API: client.chat.completions.create(...) on donkey.llm.client(), or a framework's chat-completions client.
[ok] chat completions  served on this route
```

```bash
donkey doctor --model gpt-4o     # model to test against the allow-list (default gpt-4o)
donkey doctor --json             # machine-readable checks
```

## Next steps

- [Develop locally](https://docs.donkey-kit.dev/cli/local.md): run the agent against the simulator and the test suite.
- [Configuration](https://docs.donkey-kit.dev/reference/configuration.md): every setting `init` and `doctor` read.

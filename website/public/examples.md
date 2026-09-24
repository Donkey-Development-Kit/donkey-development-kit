# Examples

The [DDK demos repo](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos)
holds runnable examples for every piece of the SDK: the governed client, typed
refusals, budget pacing, simulation, the conformance suite, telemetry,
framework objects and `last_call`. Each page in this section covers one of
those, pairs the examples that show it, and gives you the command to run them.

The repo ships **two suites on purpose**. They cover the same SDK, but they are
not interchangeable:

| | Narrative demos | OpenAI scripts |
| --- | --- | --- |
| **Where** | [`demos/claude-made/`](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/claude-made) | [`demos/human-made/openai/`](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/human-made/openai) |
| **Best for** | A room, a recording, or CI that must stay offline | A terminal you type in, or paste from |
| **Shape** | Numbered demos (`01`–`10`), each a `demo.py` + README told in acts | Straight-line OpenAI scripts (`01`–`11`), one file each |
| **Runner** | `make demo N=03`, `make offline` | `python "demos/human-made/openai/<file>.py"` |
| **Network** | Nine of ten run offline against the local simulator; demo 09 needs a live gateway | Most need gateway credentials; 09 and 11 need no gateway |
| **Output** | Masked by default | Not masked — use on a private terminal |

Every narrative demo takes `--target mock` (the default, pointing at the local
simulator) or `--target live` (your real credentials). The OpenAI scripts have
no harness: they use the same environment you already use for the SDK.

## Setup

### Create a virtual environment

```bash
git clone https://github.com/Donkey-Development-Kit/donkey-development-kit-demos.git
cd donkey-development-kit-demos
python3 -m venv .venv          # .venv/ is git-ignored
source .venv/bin/activate      # once per terminal
```

Homebrew's `python3` and most Linux distro Pythons are marked *externally
managed* (PEP 668), so a global `pip3 install` fails with
`externally-managed-environment`. Install into `.venv` instead of reaching for
`--break-system-packages`.

### Install the demo harness

```bash
python -m pip install -e .
```

This adds the shared harness to the path. It does not pin an SDK.

### Install the SDK

Pick one:

```bash
# from git, with the extras every offline demo needs
python -m pip install -e ".[sdk]"

# from git, with everything including OpenTelemetry and LangGraph
python -m pip install -e ".[full]"

# from your own SDK checkout
python -m pip install -e "../donkey-development-kit/python[llm,local,test,otel,langgraph,cli]"

# a published dev build from TestPyPI (its dependencies come from PyPI)
python -m pip install -i https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ "donkey-kit[llm,local,test,otel,langgraph,cli]"
```

  **Quote the `[...]` extras.** zsh, the macOS default shell, treats unquoted
  brackets as a glob and fails with `no matches found`.

`uv` users can replace the venv step with `uv venv` and `pip install` with
`uv pip install`.

## Live credentials

The offline narrative demos need none of this. Live runs, the LangGraph agent
example and most OpenAI scripts need three variables for the governed LLM
proxy, plus the model to ask for:

| Variable | What it is |
| --- | --- |
| `DONKEY_LLM_PROXY_URL` | The proxy's base URL, **with a trailing `/`** and no `/v1` — the OpenAI SDK appends `/responses` itself |
| `DONKEY_LLM_PROXY_CLIENT_ID` | The client id the proxy authenticates on (a header, not a bearer token) |
| `DONKEY_LLM_PROXY_CLIENT_SECRET` | The matching client secret |
| `DEMO_MODEL` | The model id the narrative demos ask for; it must be one your proxy routes |

Copy the template and fill it in:

```bash
cp .env.example .env.local     # .env.local is git-ignored
```

A shell `export` always wins over a file value, so you can skip the file and
the OpenAI scripts do not (the SDK never reads dotenv files on its own), so

```bash
set -a; source .env.local; set +a
python "demos/human-made/openai/02 - basic-responses-gw.py"
```

  The OpenAI scripts hardcode their model ids (mostly `gpt-4o`). If your proxy
  routes a different model, expect a routing refusal or a `ModelSubstituted`
  until the script's model matches the proxy's.

## Commands

```bash
make list                   # the narrative demos and what each one needs
make demo N=03              # one narrative demo
make demo N=01 ARGS="--target live"   # the same demo against your gateway
make offline                # every narrative demo that needs no credentials
make doctor                 # what is installed, and what will therefore run
make mock                   # the local simulator in the foreground, for a second pane

DEMO_PAUSE=1 make demo N=03 # pause between acts — use this when presenting
```

Flags for the demo go in `ARGS`, not on the end of the `make` line. `python
run.py 03` works too, and each narrative demo is a plain script
(`python demos/claude-made/03_budget_and_pacing/demo.py`) once the repo is
installed. `make doctor` reports what it found without printing any values.

## Credential safety

The narrative demos assume they will be screen-shared and recorded:

- **Output is masked by default.** Every value a narrative demo prints is
  scrubbed: gateway hostnames, the client id and secret, and the tenant
  identifiers that ride along in captured responses. Header *names* are shown;
  their values are not.
- **Turning masking off announces itself.** `DEMO_REDACT=0` prints a warning
  banner in every narrative demo's run context. Use it only when debugging
  privately.
- **No captured traffic is vendored.** Fixtures load from the installed SDK,
  not from copies in the demos repo.
- **`make scan` reads content, not filenames.** It fails on assigned credential
  values, bearer tokens, instance ids, UUIDs and non-allowlisted hostnames.
  `make hooks` installs it as a pre-commit hook.

```bash
make scan       # everything tracked
make hooks      # then it runs on every commit
```

  **The OpenAI scripts do not mask anything.** They print completions and
  error strings exactly as the SDK returned them. Run them on a private
  terminal, not on a shared screen or recording.

## Browse by purpose

  
    Stock client versus governed client, then `@donkey.governed` and `@donkey.tool`.
  
  
    Every captured rejection shape through `classify()`, plus `GatewayUnavailable`.
  
  
    The token window as an object; `pace(reserve=)` and `wait_for_reset()`.
  
  
    Run your refusal branch with `donkey.simulate()` and `donkey mock --scenario`.
  
  
    `pytest --donkey-conformance` grading a naive agent, then the fixed one.
  
  
    GenAI spans, `donkey.run(id=…)` correlation, and zero-config OTLP.
  
  
    Native framework objects, `connection_kwargs()`, and `resolve()`.
  
  
    A real tool-calling loop, governed end to end.
  
  
    Who served the call, what it routed to, what it cost — and `ModelSubstituted`.

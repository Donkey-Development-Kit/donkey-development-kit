# Examples

The [DDK demos repo](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos)
holds runnable examples for every piece of the SDK: the governed client, typed
refusals, budget pacing, simulation, the conformance suite, telemetry,
framework objects and `last_call`. The section has two parts:

- **[General](#general)** — one page per SDK capability, pairing the examples
  that show it with the command to run them.
- **[By framework](#by-framework)** — one page per framework, walking through
  every script in its folder: what it shows, what it needs, and what it
  prints.

The repo ships **two suites on purpose**. They cover the same SDK, but they are
not interchangeable:

| | Narrative demos | Framework scripts |
| --- | --- | --- |
| **Where** | [`demos/claude-made/`](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/claude-made) | [`demos/human-made/`](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/human-made), one folder per framework |
| **Best for** | A room, a recording, or CI that must stay offline | A terminal you type in, or paste into your own project |
| **Shape** | Numbered demos (`01`–`10`), each a `demo.py` + README told in acts | Short, top-to-bottom scripts, one file each, across ten frameworks |
| **Runner** | `make demo N=03`, `make offline` | `python "demos/human-made/<framework>/<file>.py"` |
| **Network** | Nine of ten run offline against the local simulator; demo 09 needs a live gateway | Most need gateway credentials; the `*-simulated`, `start-gateway` and `gateway-unavailable` scripts need none |
| **Output** | Masked by default | Not masked — use on a private terminal |

Every narrative demo takes `--target mock` (the default, pointing at the local
simulator) or `--target live` (your real credentials). The framework scripts
have no harness: they use the same environment you already use for the SDK.
Frameworks pin conflicting dependencies, so one virtual environment per
framework is the safe default; each framework page has its install line.

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
example and most framework scripts need three variables for the governed LLM
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
the framework scripts do not (the SDK never reads dotenv files on its own), so

```bash
set -a; source .env.local; set +a
python "demos/human-made/openai/02 - basic-responses-gw.py"
```

  Three things trip people up with the framework scripts. **The model id:**
  they hardcode `gpt-4o`, while the provisioned DDK proxies route
  `gpt-5-mini` — change the string, or expect a routing refusal or a
  `ModelSubstituted`. **Filenames have spaces:** always quote the path.
  **Policies decide refusals:** a `typed-refusals-live` script prints `NO
  REFUSAL` when the proxy does not have that policy applied — that is the
  proxy telling the truth, not the script failing.

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

  **The framework scripts do not mask anything.** They print completions and
  error strings exactly as the SDK returned them. Run them on a private
  terminal, not on a shared screen or recording.

## General

  
    Stock client versus governed client, then `@donkey.governed` and `@donkey.tool`.
  
  
    Every captured rejection shape through `classify()`, plus `GatewayUnavailable`.
  
  
    The token window as an object; `pace(reserve=)` and `wait_for_reset()`.
  
  
    Run your refusal branch with `donkey.simulate()` and `donkey mock --scenario`.
  
  
    `pytest --donkey-conformance` grading a naive agent, then the fixed one.
  
  
    GenAI spans, `donkey.run(id=…)` correlation, and zero-config OTLP.
  
  
    Native framework objects, `connection_kwargs()`, and `resolve()`.
  
  
    A real tool-calling loop, governed end to end.
  
  
    Who served the call, what it routed to, what it cost — and `ModelSubstituted`.
  

## By framework

Each framework page covers one folder of
[`demos/human-made/`](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/human-made).
How much DDK can do depends on who owns the HTTP transport: where the SDK does
(OpenAI, LangGraph, OpenAI Agents SDK, Strands, Anthropic), you get `last_call`,
run ids and `simulate()`; where the framework does, only the credential
headers go on the wire.

  
    The stock client: `last_call`, refusals, pacing, spans, streaming, guardrails, JWT wallets.
  
  
    A real `ChatOpenAI`, `typed_refusals()` out of `create_agent`, the run id in tools.
  
  
    `OpenAIResponsesModel` on the governed client, `Runner.run` with a tool.
  
  
    `OpenAIChatClient` with the governed headers; refusals from `ChatClientException`.
  
  
    `OpenAIModel(client=donkey.openai())`, tools, and a 429 Strands retries itself.
  
  
    `donkey.crewai.llm()` for a direct call and a crew; typed refusals.
  
  
    An `OpenAILike` for `complete()` and `chat()`; typed refusals.
  
  
    A `LiteLlm` model in an `InMemoryRunner` — and why its refusals are not typed.
  
  
    The native client on `/v1/messages`, `last_call` and simulated refusals.
  
  
    Plain `httpx` against a `Format=Gemini` proxy, errors typed by `classify()`.

<p align="center">
  <img src="brand/ddk-logo-stacked-black.png" alt="Donkey Development Kit (DDK)" width="180" />
</p>

<p align="center">
  <a href="https://pypi.org/project/donkey-kit/"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fpypi.org%2Fpypi%2Fdonkey-kit%2Fjson&query=%24.info.version&prefix=v&label=PyPI&logo=pypi&logoColor=white&color=blue&cacheSeconds=300" alt="donkey-kit on PyPI" /></a>
</p>

# Donkey Development Kit

An SDK for consuming **Agent Fabric** capabilities — governed model and tool
access — from your own agent framework, in your own IDE, without adopting Mule.

> **Project status — alpha.** This is an early release
> (`Development Status :: 3 - Alpha`). The **LLM data plane is live-verified**;
> most other surfaces are verification-gated (see
> [What's verified](#whats-verified-verification-discipline) below). Install it
> from PyPI with `pip install donkey-kit` — see [Install](#install).
> **Unofficial:** an independent project,
> **not** affiliated with or endorsed by Salesforce or MuleSoft.

> **Already integrated the pre-rebrand SDK?** The move to Donkey Development Kit
> is a clean break — no import shims, env fallbacks, or OpenTelemetry dual-emit.
> The [migration guide](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/MIGRATION.md)
> maps every renamed import, class, CLI, config key, and environment variable,
> and calls out the breaking OpenTelemetry attribute-namespace change.

> ### Support & trademark statement (please read)
>
> **"Agent Fabric" is a MuleSoft (Salesforce) product name, not a generic
> term.** `MuleSoft`, `Anypoint`, `Omni Gateway`, and `Agent Fabric` are
> Salesforce trademarks.
>
> **Maintainer & support.** This is an **independent, community-maintained**
> project, published under the org-scoped `Donkey-Development-Kit` name — it is **not**
> affiliated with, endorsed by, or supported by Salesforce or MuleSoft. It is
> provided **as-is, without warranty of any kind**; the maintainers triage issues
> and pull requests on a **best-effort basis, with no SLA**. Because it ships
> under a distinct, org-scoped name, only the descriptive form ("an SDK for
> MuleSoft Agent Fabric") appears in prose — the package does not represent itself
> as a first-party, official-status SDK.
>
> Licensed under [Apache-2.0](LICENSE). See
> [`docs/unsupported-boundary.md`](docs/unsupported-boundary.md) for exactly
> which platform APIs this SDK calls and their support classification.
>
> **Security.** Report vulnerabilities privately, never in a public issue. See
> [`SECURITY.md`](SECURITY.md) for supported versions and how to report.
>
> **Python versions.** CPython 3.10–3.12, each tested in CI; a version is
> dropped in the first minor release after its end of life. See
> [`docs/python-support.md`](docs/python-support.md).

## Documentation

Two audiences, two doc sets:

- **Use the SDK** → the documentation site:
  **<https://docs.donkey-kit.dev/>**. Install and
  configure, per-framework model access, the governed error taxonomy, and what
  to trust today — everything you need to point your agent at a governed proxy.
- **See it run** → runnable demos live in the companion repo
  **[donkey-development-kit-demos](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos)**:
  the framework-free client, native framework objects, the governed error
  taxonomy, and the screen-recording scripts.
- **Understand or contribute to the repo:**
  - [`ARCHITECTURE.md`](ARCHITECTURE.md) — how the SDK is built: the layered
    stack, the framework-free core, verification discipline, the error taxonomy,
    and framework tiering.
  - [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to work in the repo: the
    branch/PR/release workflow, the testing strategy, coding conventions, and
    the docs-sync rule.
  - [`docs/verified-apis.md`](docs/verified-apis.md) — the verification ledger:
    the single source of truth for what is confirmed against a real sandbox and
    what is still blocked.

## Install

```bash
pip install "donkey-kit[llm,langgraph]"   # base + raw client + one framework
```

To work on the SDK itself, install from source instead:

```bash
git clone https://github.com/Donkey-Development-Kit/donkey-development-kit.git
cd donkey-development-kit/python
pip install -e ".[llm,langgraph]"
```

Extras are one per framework (`langgraph`, `adk`, `strands`, `agent_framework`,
`openai-agents`, `anthropic`, `crewai`, `llamaindex`) plus `mcp`, `a2a`, `otel`, `cli`,
`local`, `test` (the [conformance pytest plugin](https://docs.donkey-kit.dev/testing) —
`pytest --donkey-conformance --agent=my_app.agent:build`), and `all`. `mcp` and `a2a`
are placeholders for **Roadmap** features (governed tool access, A2A agents): today they
only install the upstream `mcp` / `a2a-sdk` packages, and nothing in `donkey_kit` uses
them yet. `all` is
everything that installs together — `llm`, `langgraph`, `mcp`, `otel`, `cli`, `local`,
`test` — and leaves out the seven other framework extras, whose current upstream releases
cannot all be installed together. Add the one framework you use: `donkey-kit[all,crewai]`.
Configuration and first-agent walkthroughs live on the
[documentation site](https://docs.donkey-kit.dev/).

## Framework support

The roster is deliberately **one deep, seven shallow** (`BG §1.8`): one adapter
held to the full conformance bar, the rest supported through the three-line
`connection_kwargs()` escape hatch. Every framework below returns its framework's
**own native object** — never a wrapper.

| Tier | Frameworks | Status ([`docs/verified-apis.md` §8](docs/verified-apis.md)) |
| --- | --- | --- |
| **Deep** | The raw client (`donkey.llm.client()`) and **LangGraph** | **Conformance-tested against the simulator** in CI. LangGraph's `ChatOpenAI` constructor is signature-confirmed offline; it has had no live round-trip. |
| **Supported via `connection_kwargs()`** | Google ADK, Strands, Microsoft Agent Framework, OpenAI Agents SDK, Anthropic SDK, CrewAI, LlamaIndex | **Signature-confirmed offline**: each factory builds its native object against the installed framework (`scripts/verify_frameworks.py`), with no live round-trip and no conformance run. The exception is ADK's `gemini()`, which is **live-verified** through a `Format=Gemini` proxy. |

`connection_kwargs()` works for all eight; a second deep adapter is promoted from
demand evidence, one at a time (#223/#244) — never guessed up front. See the
[framework pages](https://docs.donkey-kit.dev/frameworks/)
for each.

## What's verified (verification discipline)

The **LLM data plane** — governed model access through the Omni Gateway proxy —
is live-verified against a real Anypoint sandbox. The framework-free client and
the framework adapters are wired to that contract, but the adapters themselves
are not live-verified: the raw client and LangGraph are conformance-tested
against the simulator, ADK's `gemini()` is live-verified, and every other
adapter constructor is signature-confirmed offline (see
[Framework support](#framework-support)).
Everything still gated raises `NotImplementedError("blocked on verification: …")`
rather than guessing at an unverified endpoint, header, or class name — that
currently includes Exchange→MCP tool discovery and the provisioning
control-plane. The adapters build their framework's native object directly; they
refuse only when the installed framework version lacks the class or field the
adapter depends on.

The discipline behind this is documented in
[`ARCHITECTURE.md` → Verification discipline](ARCHITECTURE.md#verification-discipline);
the row-by-row worklist is [`docs/verified-apis.md`](docs/verified-apis.md).

## Conformance exemptions

The [conformance plugin](https://docs.donkey-kit.dev/testing)
holds the SDK to the same bar it asks of your agent. Where a framework
legitimately cannot satisfy a scenario, the reason is asserted in code
(`KNOWN_LIMITATIONS`) and published here as credibility — never a silent skip
(the conformance kit):

| Framework | Scenario | Why it's exempt |
| --- | --- | --- |
| ADK `model()`, CrewAI | correlation ID propagated | The framework owns the transport — LiteLLM for ADK's `model()`, CrewAI's native OpenAI provider for CrewAI — so the SDK's `httpx` client cannot be injected and the correlation ID ends up per-client, not per-run. For ADK, a LiteLLM logger callback may recover trace correlation later. ADK's `gemini()` (`Format=Gemini` proxy) injects the shared client and records no exemption (#691). |
| LlamaIndex, Microsoft Agent Framework | correlation ID propagated | These adapters receive a static `default_headers` snapshot, which deliberately excludes the per-run correlation ID. Without the SDK's `httpx` client, `donkey.run(id=...)` cannot update their request headers. |
| ADK `model()`, CrewAI | gateway identity observed | The framework owns the transport (LiteLLM for ADK's `model()`, CrewAI's native OpenAI provider for CrewAI), so no response reaches the SDK's `_on_response` hook. When every resolved adapter is non-observing, `donkey.last_call` reports `UNAVAILABLE` and names them in `surface`. |
| LlamaIndex, Microsoft Agent Framework | gateway identity observed | These adapters receive `default_headers`, not the SDK's `httpx` client, so no response reaches `_on_response`. When every resolved adapter is non-observing, `donkey.last_call` reports `UNAVAILABLE` and names them in `surface`. |
| CrewAI | JWT refreshed per send | CrewAI's native OpenAI provider owns the transport and builds its own clients, so the rotating JWT the SDK adds per send never reaches its requests. `donkey.crewai.llm()` and `connection_kwargs()` raise `ConfigError` in `jwt` mode; use client-id auth with CrewAI. ADK's `model()` and `gemini()`, LlamaIndex and Microsoft Agent Framework send through the SDK's client and carry the rotating JWT on async calls ([`jwt` mode](https://docs.donkey-kit.dev/reference/configuration#jwt--model-wallet-auth-mode)). |
| CrewAI | budget refusal not retried | CrewAI wraps every LLM call in its own rate-limit retry (3 attempts) and treats any `429` as a rate limit, so a `TokenBudgetExceeded` refusal is sent 3 times. CrewAI has no setting to turn it off; the OpenAI client underneath has `max_retries=0`. Every other adapter sends a budget refusal once (#734). |

<p align="center">
  <img src="https://raw.githubusercontent.com/Donkey-Development-Kit/donkey-development-kit/main/brand/ddk-logo-stacked-black.png" alt="Donkey Development Kit (DDK)" width="180" />
</p>

<p align="center">
  <a href="https://pypi.org/project/donkey-kit/"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fpypi.org%2Fpypi%2Fdonkey-kit%2Fjson&query=%24.info.version&prefix=v&label=PyPI&logo=pypi&logoColor=white&color=blue&cacheSeconds=300" alt="donkey-kit on PyPI" /></a>
  <a href="https://pypi.org/project/donkey-kit/"><img src="https://img.shields.io/pypi/pyversions/donkey-kit?logo=python&logoColor=white" alt="Supported Python versions" /></a>
  <a href="https://github.com/Donkey-Development-Kit/donkey-development-kit/actions/workflows/ci.yml?query=branch%3Adevelop"><img src="https://img.shields.io/github/actions/workflow/status/Donkey-Development-Kit/donkey-development-kit/ci.yml?branch=develop&label=CI&logo=githubactions&logoColor=white" alt="CI status" /></a>
  <a href="https://pepy.tech/projects/donkey-kit"><img src="https://img.shields.io/pepy/dt/donkey-kit?label=downloads" alt="Total downloads" /></a>
  <a href="https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/donkey-kit?color=green" alt="License: Apache-2.0" /></a>
  <a href="https://docs.donkey-kit.dev"><img src="https://img.shields.io/badge/docs-docs.donkey--kit.dev-0A7BBB?logo=readthedocs&logoColor=white" alt="Documentation" /></a>
</p>

<p align="center">
  <a href="https://docs.donkey-kit.dev/frameworks/langgraph"><img src="https://img.shields.io/badge/LangGraph-1C3C3C?logo=langgraph&logoColor=white" alt="LangGraph" /></a>
  <a href="https://docs.donkey-kit.dev/frameworks/adk"><img src="https://img.shields.io/badge/Google_ADK-4285F4?logo=google&logoColor=white" alt="Google ADK" /></a>
  <a href="https://docs.donkey-kit.dev/frameworks/openai"><img src="https://img.shields.io/badge/OpenAI_Agents_SDK-412991" alt="OpenAI Agents SDK" /></a>
  <a href="https://docs.donkey-kit.dev/frameworks/agent-framework"><img src="https://img.shields.io/badge/Microsoft_Agent_Framework-5E5E5E" alt="Microsoft Agent Framework" /></a>
  <a href="https://docs.donkey-kit.dev/frameworks/anthropic"><img src="https://img.shields.io/badge/Anthropic-191919?logo=anthropic&logoColor=white" alt="Anthropic" /></a>
  <a href="https://docs.donkey-kit.dev/frameworks/crewai"><img src="https://img.shields.io/badge/CrewAI-FF5A50?logo=crewai&logoColor=white" alt="CrewAI" /></a>
  <a href="https://docs.donkey-kit.dev/frameworks/llamaindex"><img src="https://img.shields.io/badge/LlamaIndex-8A2BE2" alt="LlamaIndex" /></a>
  <a href="https://docs.donkey-kit.dev/frameworks/strands"><img src="https://img.shields.io/badge/Strands_Agents-FF9900" alt="Strands Agents" /></a>
  <a href="https://docs.donkey-kit.dev/telemetry"><img src="https://img.shields.io/badge/OpenTelemetry-425CC7?logo=opentelemetry&logoColor=white" alt="OpenTelemetry" /></a>
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
> Licensed under [Apache-2.0](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/LICENSE). See
> the [unsupported boundary](https://docs.donkey-kit.dev/reference/unsupported-boundary) for exactly
> which platform APIs this SDK calls and their support classification.
>
> **Security.** Report vulnerabilities privately, never in a public issue. See
> [`SECURITY.md`](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/SECURITY.md) for supported versions and how to report.
>
> **Python versions.** CPython 3.10–3.14, each tested in CI; a version is
> dropped in the first minor release after its end of life. See
> [`docs/python-support.md`](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/python-support.md).

<!-- toc -->
**Contents**

- [Documentation](#documentation)
- [Install](#install)
- [Framework support](#framework-support)
- [What's verified (verification discipline)](#whats-verified-verification-discipline)
- [Conformance exemptions](#conformance-exemptions)
<!-- tocstop -->

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
  - [`ARCHITECTURE.md`](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/ARCHITECTURE.md) — how the SDK is built: the layered
    stack, the framework-free core, verification discipline, the error taxonomy,
    and framework tiering.
  - [`CONTRIBUTING.md`](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/CONTRIBUTING.md) — how to work in the repo: the
    branch/PR/release workflow, the testing strategy, coding conventions, and
    the docs-sync rule.
  - [`docs/verified-apis.md`](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md) — the verification ledger:
    the single source of truth for what is confirmed against a real sandbox and
    what is still blocked.

## Install

```bash
pip install "donkey-kit[llm,langgraph]"   # base + raw client + one framework
```

To work on the SDK itself, install from source with the contributor tooling
(the `dev` dependency group needs pip 25.1 or later):

```bash
git clone https://github.com/Donkey-Development-Kit/donkey-development-kit.git
cd donkey-development-kit/python
pip install -e ".[llm,cli]" --group dev
```

Extras are one per framework (`langgraph`, `adk`, `strands`, `agent_framework`,
`openai-agents`, `anthropic`, `crewai`, `llamaindex`) plus `otel`, `cli`, `local`,
`a2a` (the official `a2a-sdk`, for `donkey.serve()`), `test` (the [conformance pytest plugin](https://docs.donkey-kit.dev/testing) —
`pytest --donkey-conformance --donkey-agent=my_app.agent:build`), and `all`. `all` is
everything a user runs that installs together — `llm`, `langgraph`, `otel`, `cli`,
`local`, `a2a` — with no test runner, so add `test` for the conformance plugin:
`donkey-kit[all,test]`. It leaves out the seven other framework extras, because
whether they install together depends on their upstream releases. The generated
[co-installability table](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/co-installability.md) shows which combinations resolve, re-checked
nightly. Add the one framework you use: `donkey-kit[all,crewai]`.
Configuration and first-agent walkthroughs live on the
[documentation site](https://docs.donkey-kit.dev/).

## Framework support

The roster is deliberately **two deep, six shallow** (`BG §1.8`): LangGraph and
the OpenAI Agents SDK are held to the full conformance bar, the rest supported
through the three-line `connection_kwargs()` escape hatch. Every framework below returns its framework's
**own native object** — never a wrapper.

<!-- BEGIN GENERATED: framework-status (edit docs/verified-apis.md §8, then run python/scripts/generate_docs.py) -->
Each factory's status is its constructor row in the [`docs/verified-apis.md` §8](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md); its
depth is the adapter roster's `conformance_tested`.

| Framework | Factory | Depth | Ledger status |
|---|---|---|---|
| LangGraph | `donkey.langgraph.chat_model()` | **Conformance-tested** | **Live-verified** |
| Google ADK | `donkey.adk.model()` | `connection_kwargs()` | **Live-verified** |
| Google ADK | `donkey.adk.gemini()` | `connection_kwargs()` | **Live-verified** |
| Strands | `donkey.strands.model()` | `connection_kwargs()` | **Live-verified** |
| MS Agent Framework | `donkey.agent_framework.chat_client()` | `connection_kwargs()` | **Live-verified** |
| OpenAI Agents SDK | `donkey.openai_agents.model()` | **Conformance-tested** | **Live-verified** |
| Anthropic SDK | `donkey.anthropic.client()` | `connection_kwargs()` | **Live-verified** |
| CrewAI | `donkey.crewai.llm()` | `connection_kwargs()` | **Live-verified** |
| LlamaIndex | `donkey.llamaindex.llm()` | `connection_kwargs()` | **Live-verified** |
<!-- END GENERATED: framework-status -->

**Signature-confirmed** means the factory builds its native object against the
installed framework (`scripts/verify_frameworks.py`), with no live round-trip.
**Live-verified** means a real round-trip through a governed proxy. The raw
client (`donkey.llm.client()`) is conformance-tested too.

`connection_kwargs()` works for all eight; a deep adapter is promoted from demand
evidence, one at a time — never guessed up front. The OpenAI Agents SDK was the
second, on Phase 1 demand (#223); the next is #244. See the
[framework pages](https://docs.donkey-kit.dev/frameworks/)
for each. Which framework versions are supported and tested is on the site's
[version compatibility](https://docs.donkey-kit.dev/frameworks/compatibility)
page and in [`docs/framework-support.md`](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/framework-support.md).

## What's verified (verification discipline)

The **LLM data plane** — governed model access through the Omni Gateway proxy —
is live-verified against a real Anypoint sandbox. The framework-free client and
the framework adapters are wired to that contract; the raw client is
conformance-tested against the simulator. For the adapters (each factory is
listed under [Framework support](#framework-support)):

<!-- BEGIN GENERATED: framework-status-summary (edit docs/verified-apis.md §8, then run python/scripts/generate_docs.py) -->
LangGraph and OpenAI Agents SDK are conformance-tested against the simulator in CI.
Each adapter factory's constructor row in §8 of the ledger reads **live-verified**.
<!-- END GENERATED: framework-status-summary -->

Everything still gated raises `NotImplementedError("blocked on verification: …")`
rather than guessing at an unverified endpoint, header, or class name — that
currently includes Exchange publication and the CrewAI MCP binding. MCP tool
discovery and the other framework bindings are verified against a local MCP
server, not yet through the governed gateway; their types live in `donkey_kit.experimental`, outside the stable namespace. The SDK
does not ship a provisioning control plane (ADR 0008 in `docs/adr/`). The adapters build their framework's native object directly; they
refuse only when the installed framework version lacks the class or field the
adapter depends on.

The discipline behind this is documented in
[`ARCHITECTURE.md` → Verification discipline](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/ARCHITECTURE.md#verification-discipline);
the row-by-row worklist is [`docs/verified-apis.md`](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md).

## Conformance exemptions

The [conformance plugin](https://docs.donkey-kit.dev/testing)
holds the SDK to the same bar it asks of your agent. Where a framework
legitimately cannot satisfy a scenario, the reason is asserted in code
(`KNOWN_LIMITATIONS`) and published here as credibility — never a silent skip
(the conformance kit):

| Framework | Scenario | Why it's exempt |
| --- | --- | --- |
| CrewAI | correlation ID propagated | CrewAI's native OpenAI provider builds both its sync `OpenAI` and its `AsyncOpenAI` from one `client_params` dict, and with an interceptor set it replaces `http_client` with its own `httpx` client, so the SDK's async client cannot be injected and the correlation ID ends up per-client, not per-run. ADK (`model()` and `gemini()`), LlamaIndex and Microsoft Agent Framework send through the SDK's shared client and record no exemption (#691, #740). |
| CrewAI | gateway identity observed | For the same reason, no response reaches the SDK's `_on_response` hook. When every resolved adapter is non-observing, `donkey.last_call` reports `UNAVAILABLE` and names them in `surface`. |
| CrewAI | JWT refreshed per send | CrewAI's native OpenAI provider owns the transport and builds its own clients, so the rotating JWT the SDK adds per send never reaches its requests. `donkey.crewai.llm()` and `connection_kwargs()` raise `ConfigError` in `jwt` mode; use client-id auth with CrewAI. ADK's `model()` and `gemini()`, LlamaIndex and Microsoft Agent Framework send through the SDK's client and carry the rotating JWT on async calls ([`jwt` mode](https://docs.donkey-kit.dev/reference/configuration#jwt--model-wallet-auth-mode)). |
| CrewAI | budget refusal not retried | CrewAI wraps every LLM call in its own rate-limit retry (3 attempts) and treats any `429` as a rate limit, so a `TokenBudgetExceeded` or `RequestRateLimitExceeded` refusal is sent 3 times. CrewAI has no setting to turn it off; the OpenAI client underneath has `max_retries=0`. Every other adapter sends a budget refusal once (#734). |
| CrewAI | typed refusal bridged | CrewAI's native OpenAI provider owns the transport, so its errors come from a client the SDK never saw, and `donkey.run()` and `typed_refusals()` cannot tell a gateway refusal from any other failure there and pass those errors through (#724). ADK's `gemini()` and `model()` send through the SDK's transport and are bridged (conformance-tested in #955 and #969). LiteLLM, under `model()`, re-raises a refusal as its own exception, and the adapter's `llm_client` marks that exception with the refusal the SDK's transport received. |
| CrewAI | refusal handler cannot un-refuse | CrewAI's native OpenAI provider owns the transport, so its refusals never reach the SDK's `_on_refusal` hook and handlers registered with [`donkey.on(...)`](https://docs.donkey-kit.dev/refusal-handlers) do not run for its calls. Every other adapter runs them, and none can turn a refusal into a success (#208, #209). |
| CrewAI | custom policy typed | For the same reason, a refusal shape registered with [`classify.register()`](https://docs.donkey-kit.dev/custom-policies) is neither typed by `donkey.run()` nor seen by `donkey.on(...)` handlers for CrewAI's calls. Every other adapter raises the registered type, runs its handlers and records its slug as `donkey.policy.type` (#210). |
| CrewAI | agent kill stops cleanly | CrewAI's native OpenAI provider owns the transport, so a kill-switch refusal never reaches the SDK's transport: it does not stop [`donkey.budget.pace()`](https://docs.donkey-kit.dev/budget) loops or flush spans, and CrewAI's own rate-limit retry decides whether the call is re-sent. Every other adapter sends the kill once, stops every pace loop on the same `Donkey`, and flushes spans before the `AgentKilled` reaches your code (#220). |
| CrewAI | approval round trip | CrewAI's native OpenAI provider owns the transport, so when an approved [`@donkey.tool(approval="required")`](https://docs.donkey-kit.dev/hitl) resumes, its model call carries neither the approval's correlation ID nor a typed refusal. The pause and the approver's span event work as for any Python code. Every other adapter resumes the call under the decision's correlation ID, and the gateway still judges it: a refusal comes back typed (#211, #212). The OpenAI Agents SDK pauses on its own `needs_approval` and resumes from the `RunState` (#213). |

Each exemption matches what the adapter reports in `donkey.<framework>.capabilities()` (a frozen `AdapterCapabilities` per factory), and a unit test keeps the two in step (#726).

<!-- Generated from the repository README.md by python/scripts/generate_docs.py; edit that file, then run the script. -->

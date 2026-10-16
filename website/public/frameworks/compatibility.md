# Version compatibility

Status: Live. Shipped, and verified against a real governed gateway.

Which versions of each framework `donkey-kit` supports. Install a framework
with its extra, for example `pip install "donkey-kit[langgraph]"`; the extra
declares the minimum versions below.

**Supported** means any release at or above the minimum, except a version
listed as known incompatible. The extras set no upper bounds, so a fresh
install takes each framework's newest release.

- **Tested on every PR** is the exact version every pull request installs
  and tests the adapter against, on Python 3.12.
- **Minimum supported** is tested every night: CI installs each extra at
  its minimum versions on the oldest supported Python and runs the tests.
- **Newer releases** are tested every night: CI installs each framework's
  newest release and runs the same tests. A failing night opens a public
  [`nightly-failure` issue](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues?q=is%3Aissue%20is%3Aopen%20label%3Anightly-failure).
- **Known incompatible** lists the open rows of the
  [known upstream incompatibilities](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md#81-known-upstream-incompatibilities-floors-not-ceilings). A break is fixed in the SDK or recorded there, never
  pinned away.

[![Nightly matrix](https://github.com/Donkey-Development-Kit/donkey-development-kit/actions/workflows/nightly-matrix.yml/badge.svg)](https://github.com/Donkey-Development-Kit/donkey-development-kit/actions/workflows/nightly-matrix.yml)

| Framework | Tier | Extra | Package | Minimum supported | Tested on every PR | Known incompatible |
|---|---|---|---|---|---|---|
| [LangGraph](https://docs.donkey-kit.dev/frameworks/langgraph.md) | Conformance-tested | `[langgraph]` | `langgraph` | 0.4 | 1.2.15 | `langgraph==0.5.0` with `langchain-core>=1.0` |
|  |  |  | `langchain-openai` | 0.3.9 | 1.7.2 |  |
|  |  |  | `langchain-core` | 0.3.45 | 1.6.11 |  |
| [Google ADK](https://docs.donkey-kit.dev/frameworks/adk.md) | `connection_kwargs()` | `[adk]` | `google-adk` | 2.4 | 2.12.0 | None known |
|  |  |  | `litellm` | 1.84 | 1.105.1 |  |
| [Strands](https://docs.donkey-kit.dev/frameworks/strands.md) | `connection_kwargs()` | `[strands]` | `strands-agents` | 1.57.1 | 1.61.1 | None known |
| [MS Agent Framework](https://docs.donkey-kit.dev/frameworks/agent-framework.md) | `connection_kwargs()` | `[agent_framework]` | `agent-framework` | 1.13 | 1.22.0 | None known |
| [OpenAI Agents SDK](https://docs.donkey-kit.dev/frameworks/openai.md) | `connection_kwargs()` | `[openai-agents]` | `openai-agents` | 0.20 | 0.24.0 | None known |
|  |  |  | `openai` | 2.45 | 3.31.0 |  |
| [Anthropic SDK](https://docs.donkey-kit.dev/frameworks/anthropic.md) | `connection_kwargs()` | `[anthropic]` | `anthropic` | 0.116 | 1.15.0 | None known |
| [CrewAI](https://docs.donkey-kit.dev/frameworks/crewai.md) | `connection_kwargs()` | `[crewai]` | `crewai` | 1.15.3 | 1.15.28 | None known |
| [LlamaIndex](https://docs.donkey-kit.dev/frameworks/llamaindex.md) | `connection_kwargs()` | `[llamaindex]` | `llama-index-llms-openai-like` | 0.8 | 0.8.1 | None known |

## Installing several frameworks together

Each extra installs on its own and next to `[all]`. Whether two framework
extras resolve in one environment is re-checked every night; see the
[co-installability table](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/co-installability.md).

## More detail

The [framework support reference](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/framework-support.md) in the repository has the
same table, where each column comes from, which CI job tests each version,
and the full list of recorded upstream changes, including those the SDK
already handles.

# Framework support

<!-- GENERATED FILE: do not edit by hand. Refresh with
     `python scripts/generate_docs.py` from python/ (#1068). -->

Which versions of each agent framework `donkey-kit` supports, and how CI
tests them. The site's [version compatibility](https://docs.donkey-kit.dev/frameworks/compatibility) page shows
the same table to SDK users. Both are written from the sources below by
`python/scripts/generate_docs.py`, and `tests/unit/test_generated_docs.py`
fails when either is stale.

## Supported versions

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
  [`verified-apis.md` §8.1](verified-apis.md#81-known-upstream-incompatibilities-floors-not-ceilings). A break is fixed in the SDK or recorded there, never
  pinned away.

[![Nightly matrix](https://github.com/Donkey-Development-Kit/donkey-development-kit/actions/workflows/nightly-matrix.yml/badge.svg)](https://github.com/Donkey-Development-Kit/donkey-development-kit/actions/workflows/nightly-matrix.yml)

| Framework | Tier | Extra | Package | Minimum supported | Tested on every PR | Known incompatible |
|---|---|---|---|---|---|---|
| LangGraph | **Conformance-tested** | `[langgraph]` | `langgraph` | 0.4 | 1.2.18 | `langgraph==0.5.0` with `langchain-core>=1.0` |
|  |  |  | `langchain-openai` | 0.3.9 | 1.7.3 |  |
|  |  |  | `langchain-core` | 0.3.45 | 1.7.1 |  |
| Google ADK | `connection_kwargs()` | `[adk]` | `google-adk` | 2.4 | 2.13.0 | None known |
|  |  |  | `litellm` | 1.84 | 1.106.6 |  |
| Strands | `connection_kwargs()` | `[strands]` | `strands-agents` | 1.57.1 | 1.62.0 | None known |
| MS Agent Framework | `connection_kwargs()` | `[agent_framework]` | `agent-framework` | 1.13 | 1.23.0 | None known |
| OpenAI Agents SDK | **Conformance-tested** | `[openai-agents]` | `openai-agents` | 0.20 | 0.24.0 | None known |
|  |  |  | `openai` | 2.45 | 3.41.0 |  |
| Anthropic SDK | `connection_kwargs()` | `[anthropic]` | `anthropic` | 0.116 | 2.3.0 | None known |
| CrewAI | `connection_kwargs()` | `[crewai]` | `crewai` | 1.15.3 | 1.15.33 | None known |
| LlamaIndex | `connection_kwargs()` | `[llamaindex]` | `llama-index-llms-openai-like` | 0.8 | 0.8.1 | None known |

## Where each column comes from

| Column | Source |
|---|---|
| Tier | `conformance_tested` on the adapter's `ADAPTERS` entry |
| Extra, Package | the adapter's extra in `python/pyproject.toml` |
| Minimum supported | the `>=` floor of each requirement in that extra (ADR 0007 rule 1). [`verified-apis.md` §8.3](verified-apis.md#83-extras-floors-lowest-verified-versions) records why each floor is where it is. |
| Tested on every PR | the package's pin in `python/constraints/adapter-contract-<extra>-py3.12.txt` (ADR 0007 rule 3) |
| Known incompatible | the [`verified-apis.md` §8.1](verified-apis.md#81-known-upstream-incompatibilities-floors-not-ceilings) rows that name one of the extra's packages and are not `MITIGATED` |

## How CI tests each framework

| Job | Workflow | When | What it installs |
|---|---|---|---|
| `adapter-contract (<extra>)` | `ci.yml` | every PR | the extra at its lock, on Python 3.12: the *Tested* column |
| `framework-legs (<framework>)` | `nightly-matrix.yml` | daily | the extra resolved fresh: each framework's newest release |
| `lowest-direct (<extra>)` | `nightly-matrix.yml` | daily | the extra at its floors (`uv --resolution lowest-direct`), on the oldest supported Python: the *Minimum* column. `scripts/check_floors.py` fails a floor the resolver could not reach. |
| `co-installability` | `nightly-matrix.yml` | daily | every pair of extras; see [`co-installability.md`](co-installability.md) |

A red nightly opens or updates one issue labelled `nightly-failure`.
After a green nightly on `develop`, `lock-refresh.yml` recompiles the lock
and opens one PR that moves it, with these pages regenerated
([`ci.md`](ci.md#lock-refreshyml-the-lock-bump)).

## Changing a version

A PR that moves a lock, an extra's floor or a §8.1 row regenerates both
pages in the same commit. From `python/`:

```bash
python scripts/compile_constraints.py   # only when the lock moves
python scripts/generate_docs.py         # this page and the site page
(cd ../website && npm run generate:llms)   # the site's llms outputs
```

Never add an upper bound to make a version pass (`§8.4`): fix the
adapter, or record the break as a §8.1 row.

## Upstream changes on record

Every [`verified-apis.md` §8.1](verified-apis.md#81-known-upstream-incompatibilities-floors-not-ceilings) row,
including the mitigated ones, which the SDK already handles:

| Dependency | Breaks at | Status |
|---|---|---|
| `openai` | `>=3.0` | MITIGATED |
| The framework extras together (`adk`, `agent_framework`, `anthropic`, `crewai`, …) | `anthropic>=1.0`, `google-adk>=2.10`, `agent-framework>=1.19` | MITIGATED |
| `anthropic` | `>=1.0` | MITIGATED |
| `langgraph` × `langchain-core` | `langgraph==0.5.0` with `langchain-core>=1.0` | OPEN (upstream) |

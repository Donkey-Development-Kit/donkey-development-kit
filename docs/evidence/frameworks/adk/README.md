# Google ADK installed-package verification

Date: 2026-09-26. Base commit: `ee56f8112f633b0368629376b59fcf5fc1f1f2d1`.
Python 3.11.16, macOS arm64. Issue #636.

## Scope and results

`google-adk==2.10.0`; `google-genai==2.25.0`; `litellm==1.102.1`; `mcp==1.28.1`.

- [signature.txt](signature.txt): unmodified `verify_frameworks.py --only adk --emit-verified` stdout/stderr; exit 0.
- [apis.txt](apis.txt): executable §9/§10 probe stdout/stderr; exit 0. Network access is denied by a Python audit hook.
- [requirements.txt](requirements.txt): exact installed distribution versions (DDK itself excluded), recorded after installing `.[all,dev]` and MCP extension distributions. `pip check` passed. These are evidence pins, not changes to project dependency floors.

No remote MCP connection, gateway round-trip, or conformance promotion is established. DDK binding and descriptor guards remain intact.

## Reproduce

From `python/`, in a disposable Python 3.11 environment:

```bash
python -m pip install -r ../docs/evidence/frameworks/adk/requirements.txt
python -m pip install -e .
export DONKEY_LLM_PROXY_URL=https://placeholder.invalid/proxy/
export DONKEY_LLM_PROXY_CLIENT_ID=placeholder-cid
export DONKEY_LLM_PROXY_CLIENT_SECRET=placeholder-secret
export LITELLM_LOCAL_MODEL_COST_MAP=True
export OTEL_SDK_DISABLED=true
export CREWAI_TELEMETRY_DISABLED=true
export CREWAI_STORAGE_DIR=/tmp/ddk-crewai-storage
python scripts/verify_frameworks.py --only adk --emit-verified
python scripts/verification/verify_adk_apis.py
```

Clear inherited proxy settings and `ANYPOINT_*` credentials for these offline runs.
On macOS CrewAI initializes its own application-data/credential directory on import;
filesystem permission is needed even though these probes do not authenticate or call a model.
The additional MCP packages (`langchain-mcp-adapters`, `crewai-tools`, and
`llama-index-tools-mcp`) are not all supplied by DDK's `[all]` extra.

The full dependency environment was resolved with `uv pip install -e '.[all,dev]'`
after pip's resolver backtracked; then the three MCP extension distributions were
installed. The resulting environment uses the mutually compatible versions listed
here, not necessarily each distribution's independently newest release.

## Validation

Each branch ran the repository pre-PR gate in a separate `.[dev,llm,cli]`
environment: [pytest](gate-pytest.txt), [mypy](gate-mypy.txt),
[Ruff](gate-ruff.txt), [import boundaries](gate-imports.txt), and
[diff whitespace](gate-diff.txt). The standard suite passed 682 tests with
47 optional-dependency skips and 6 opt-in tests deselected. No tests were changed.

The separately installed framework-free `.[dev]` baseline passed 640 tests
with 59 optional-dependency skips ([output](gate-base-only.txt)). The SDK source
and existing tests are identical in these documentation/probe branches.

The all-framework environment also passed the full suite: 771 passed, 1 skipped,
13 opt-in tests deselected ([output](gate-all-frameworks.txt)). This runs against
the unchanged SDK/test baseline; telemetry was enabled for telemetry assertions.

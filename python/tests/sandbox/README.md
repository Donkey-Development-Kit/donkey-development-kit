# Sandbox suite — live tests against the provisioned LLM Gateway proxies

`@pytest.mark.sandbox` tests that call the **real** LLM Gateway proxies
provisioned in
[`donkey-development-kit-provisioning`](https://github.com/Donkey-Development-Kit/donkey-development-kit-provisioning).
This is the live twin of the fixture-driven `tests/unit/test_llm_proxy_contract.py`:
that file replays captured contracts, this one confirms them against the
deployed proxies — and captures the two rejection shapes still pending live
capture (#253). Tracked in #400.

## Off by default

The `sandbox` marker is deselected by `addopts` in `python/pyproject.toml`, so
a plain `pytest -q` collects **zero** of these. Unlike the conformance kit's
never-skip rule, a skip here is correct: the marker gates *infra availability*
(a real proxy plus its consumer creds), not framework support.

## Running it

```bash
cd python
cp tests/sandbox/proxies.toml.example tests/sandbox/proxies.toml   # then fill in real base URLs
# export each proxy's consumer creds (see below), then:
DONKEY_SANDBOX_TESTS=1 pytest -q -m sandbox
DONKEY_SANDBOX_TESTS=1 pytest -q -m sandbox -s   # -s to see #253 capture output
```

## Configuration — `proxies.toml` (gitignored)

Each `[[proxy]]` entry maps a provisioned proxy to the consumer credentials
minted for it. The manifest holds **no secrets** — only a `base_url`, the routed
`model`, and the *names* of the two env vars holding that proxy's `client_id` /
`client_secret`:

```toml
[[proxy]]
key = "openai-model-routing"
base_url = "https://<host>:8081/<base-path>"   # ingress WITHOUT /v1 (§2)
model = "gpt-5-mini"
client_id_env = "DDK_SANDBOX_OPENAI_ROUTING_CLIENT_ID"
client_secret_env = "DDK_SANDBOX_OPENAI_ROUTING_CLIENT_SECRET"
```

Fill `base_url` from the provisioning repo's README "Current proxies" table
(host + port `8081` + the proxy's base path). Mint the consumer credential pair
per proxy with the `ddk-request-llm-proxy-access` skill and export the two env
vars each entry names — keep them in the SDK repo's gitignored `.env`, never in
this repo and never in `proxies.toml`.

A proxy whose creds are absent from the environment is skipped, not an error, so
you can configure one proxy at a time.

## Why the manifest exists (multi-target)

`core/config.DonkeyConfig` resolves a single `DONKEY_LLM_PROXY_*` triple, but the
provisioning repo hosts several proxies, each with its own credential pair. The
manifest is how the suite addresses them all without contorting the single-triple
env config — so `core/` is untouched by this suite (#400).

## Capturing the #253 rejection bodies

`test_injection_guard_rejection_classifies_and_captures` prints the live status,
headers, and body when the Regex Prompt Guard rejects. To turn that into a
verified contract: capture it into `tests/fixtures/anypoint/llm_proxy/` with
provenance recorded in that directory's README (org id, environment, proxy
version, what produced the rejection, confirmation nothing sensitive survived),
then flip the injection / content-moderation rows in `docs/verified-apis.md`
and set `verified=True` in `core/_verify.py`.

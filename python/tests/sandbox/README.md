# Sandbox suite — live tests against the provisioned LLM Gateway proxies

`@pytest.mark.sandbox` tests that call the **real** LLM Gateway proxies
provisioned in
[`donkey-development-kit-provisioning`](https://github.com/Donkey-Development-Kit/donkey-development-kit-provisioning).
This is the live twin of the fixture-driven `tests/unit/test_llm_proxy_contract.py`:
that file replays captured contracts, this one confirms them against the
deployed proxies — and prints a raw capture of any rejection it provokes, the
path that captured the #253 rejection shapes. Tracked in #400.

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
base_url = "https://<host>/<base-path>"   # no port, no /v1 (docs/verified-apis.md §2)
model = "gpt-5-mini"
client_id_env = "DDK_SANDBOX_OPENAI_ROUTING_CLIENT_ID"
client_secret_env = "DDK_SANDBOX_OPENAI_ROUTING_CLIENT_SECRET"
```

Fill `base_url` from the provisioning repo's README "Current proxies" table:
the gateway host plus the proxy's base path, **with no port**. Port `8081` in
that table is the gateway-internal ingress port, which CloudHub maps to the
deployment's public URL on 443 — a `base_url` carrying it will not resolve.
Mint the consumer credential pair
per proxy with the `ddk-request-llm-proxy-access` skill and export the two env
vars each entry names — keep them in the SDK repo's gitignored `.env`, never in
this repo and never in `proxies.toml`.

A proxy whose creds are absent from the environment is skipped, not an error, so
you can configure one proxy at a time.

## The weekly live-contract check (#753)

`.github/workflows/live-contract-check.yml` runs this suite every Monday at
07:00 UTC, and on `workflow_dispatch`. It covers the `openai-model-routing`
proxy only. It reads the proxy's base URL and consumer pair from the
`live-sandbox` GitHub environment's three secrets (`DONKEY_LLM_PROXY_URL`,
`DONKEY_LLM_PROXY_CLIENT_ID`, `DONKEY_LLM_PROXY_CLIENT_SECRET`) and writes a
one-entry `proxies.toml`. Tests for the other proxies skip.

The contract-drift guard is
`test_openai_routing_response_shape_matches_fixture`. It compares the live
response's *shape* (keys, and whether each value is an object, array or
scalar) with `responses.success.body.json`. A key that disappears, or a value
that changes kind, fails the run. A key the live body adds only warns. Output
items are matched by their `type`, so a reasoning model's leading `reasoning`
item does not count as drift. `test_shape_diff_reports_breaking_and_added_keys`
is the offline self-check of that comparison; it needs no proxy:
`pytest -q -m sandbox -k shape_diff`.

The first step fails the run when any of the three secrets is empty, because
otherwise every test would skip and the run would pass having checked nothing.
Any failure opens, or comments on, one open issue labelled
`live-contract-drift`.

## Why the manifest exists (multi-target)

`core/config.DonkeyConfig` resolves a single `DONKEY_LLM_PROXY_*` triple, but the
provisioning repo hosts several proxies, each with its own credential pair. The
manifest is how the suite addresses them all without contorting the single-triple
env config — so `core/` is untouched by this suite (#400).

## Capturing a rejection body

Every shape #253 tracked is now LIVE-captured (`docs/verified-apis.md` §4). The
procedure below is how the next one gets captured, e.g. a federated-guardrail
verdict (#305).

`test_injection_guard_rejection_classifies_and_captures` prints the live status,
headers, and body when the Regex Prompt Guard rejects. To turn that into a
verified contract: capture it into `src/donkey_kit/simulator/_fixtures/anypoint/llm_proxy/` with
provenance recorded in that directory's README (org id, environment, proxy
version, what produced the rejection, confirmation nothing sensitive survived),
then flip the injection / content-moderation rows in `docs/verified-apis.md`
and, if a `core/_verify.py` placeholder backs the shape, replace it with a plain
constant.

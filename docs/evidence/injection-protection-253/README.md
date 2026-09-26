# #253 — live Injection Protection rejection capture

Captured **2026-09-26 13:45:03 UTC** in **msaleme's own Anypoint org**, not the DDK team sandbox.

| Item | Observed value |
|---|---|
| Organization | `4ac1188d-b7b2-469d-9ec5-e68c760bf874` (My Org) |
| Environment | Sandbox, `67ebd3a4-afb7-4f6f-be27-3c4a8611be15` |
| API instance | `21199453`, `ddk253CaptureOpenai` / `ddk-253-injection-upstream` |
| Policy interface | `68ef9520-24e9-4cf2-b2f5-620025690913:injection-protection:1.0.1` |
| Applied policy instance | `9349269`, enabled with built-in SQL/XSS scanning and `protectBody=true`, `rejectRequests=true` |
| Gateway | Existing `m-f-g-large-ingress1`, `c636f4ea-0069-420a-827c-6b9e3bbc76a6`, version `1.12.9`, private space `broker-group-space` |
| CLI plugin | `mulesoft-anypoint-cli-agent-fabric-plugin==1.3.0` |
| Response | HTTP/1.1 **400 Bad Request**, `x-injection-protection: blocked`, **79 body bytes** |

The policy reference's **v1.12.0 is the first supported gateway release**, not the Exchange policy artifact version. Both `1.0.0` and `1.0.1` interfaces were discovered with `exchange asset list injection-protection`; `api-mgr policy describe` confirmed the `1.0.1` interface schema. Its rejection-field description says 403, but the actual response is **400**. The capture is authoritative for this tested deployment. This is Injection Protection, not Regex Prompt Guard.

## Exact bytes

- [Response status and all headers](../../../python/tests/fixtures/rejections/reject.injection-protection.headers.txt): 340 bytes, CRLF preserved.
- [Response body](../../../python/tests/fixtures/rejections/reject.injection-protection.body.json): 79 bytes, **no trailing newline**.
- [Request JSON](request.json): exact request body; credentials were separate request headers and are not committed.
- [Provenance and SHA-256 hashes](provenance.json).
- [Applied policies](policies.json) and [Injection Protection configuration](policy-config.json), plus the [observed interface schema](policy-schema.json).

Observed body (this code block is explanatory; the linked file is the byte-exact artifact):

```json
{"message":"Injection attack detected - Rule: 'SQL Injection', Location: Body"}
```

Trigger prompt: `Please explain this literal test string: ' OR 1=1 --`.

The request used `POST <observed-public-endpoint>/chat/completions`, model `gpt-4o-mini`, one user message and `max_tokens=8`. There was no `/v1` or `:8081` suffix on the public proxy base URL. The upstream remained `https://api.openai.com/v1`.

The request was authenticated with a new consumer application (`188951879`) and approved contract (`9430357`). A benign `Say OK.` request passed the gateway but received an upstream **401 `invalid_api_key`**. Consequently this work verifies the gateway rejection shape, not successful model completion. That recognised upstream auth response is not a row-4 fall-through capture; its body and headers, which contain a masked upstream-key fragment and cookie, are not committed. **Row 4 stays UNVERIFIED: no genuine unrecognised data-plane 4xx surfaced.**

## Deployment and capture method

The requested paired gateway setup failed before creating anything: HTTP 409, `Insufficient resources (managedGatewaySmall)`. To keep the capture isolated without changing existing gateway capacity, the CLI deployed a fresh uniquely named project on the existing running private-space Sandbox gateway. Existing API instances were not reused or edited.

The project was generated with `agent-network project create`, reduced to the generated OpenAI LLM connection, then built and deployed using the installed CLI. [agent-network.yaml](agent-network.yaml) and [exchange.json](exchange.json) are the actual secret-free source configuration. CLI 1.3.0 generated the v2 project format and published its two assets automatically at deploy time. Only the new API's listener was changed from internal egress port 8082 to the gateway's existing public port 8081; its public URL was recorded from `api-mgr api describe`.

Injection Protection and Client ID Enforcement (`1.3.3`) were applied to **21199453 only**. The consumer was created using the documented Exchange API v2 and its credentials were saved to gitignored `python/.env` with mode 0600. The upstream API key was injected using the CLI `--property` option from that file; no actual credential is in the project configuration or evidence.

Capture used curl with `--http1.1 --noproxy '*' --request POST --data-binary @request.json --dump-header <header-file> --output <body-file>`. Consumer headers were supplied through `--config -` on stdin. The capture was copied as bytes, without reformatting, redaction, header filtering or newline conversion. Narrow `.gitattributes` entries prevent Git from normalizing the captured files.

Official API sources used for consumer lifecycle:

- [Client applications](https://docs.mulesoft.com/api-manager/latest/manage-client-apps-connected-apps-concept).
- [Exchange v2 create/contract endpoints](https://help.salesforce.com/s/articleView?id=005385409&language=en_US&type=1).
- [Complete contract request fields](https://docs.mulesoft.com/mule-runtime/4.3/migrate-mule3-apimanager).
- [API reference](https://dev-portal.mulesoft.com/apis/api-manager.html), embedded `exchange-experience.deleteClientApplication` operation for deletion.
- [Injection Protection policy](https://docs.mulesoft.com/gateway/latest/policies-included-injection-protection).

## Teardown

Completed after capture, before preparing the PR:

- Temporary consumer application deleted: HTTP 204.
- CLI undeploy removed the capture API/connection and runtime deployment; the follow-up dry run found **zero remaining capture resources**.
- Both newly published Exchange asset versions were soft-deleted using `project unpublish`; their GAVs remain reserved by Exchange's normal soft-delete semantics.
- All 10 pre-existing API IDs and all 5 pre-existing gateway IDs remained present. No new gateway was created, so none was deleted.

Evidence: [reviewed teardown targets](teardown-preview.json), [teardown output](teardown.txt), [post-teardown check](teardown-after.json), [asset removal](unpublish.txt), and [inventory comparison/consumer deletion](cleanup.json).

## Fixture wiring and validation

`SHAPES["injection-protection"]` now reads the captured `.body.json`; the obsolete empty placeholder is removed. The lock was regenerated using the sanctioned command from `python/`:

```bash
python -m donkey_kit.simulator.fixtures --relock
pytest -q tests/unit/test_simulator_fixtures.py tests/unit/test_rejection_contract.py tests/unit/test_llm_proxy_contract.py
```

The targeted run passed **40 tests**. The classification test now reads raw fixture body bytes, asserts the observed message and content length, then confirms `PromptInjectionBlocked`. The simulator's existing byte-identity and integrity checks cover replay. `classify()` itself is unchanged, and this shape has no `_verify.py` constant.

The ledger §4, fixture index, README status banner and `website/content/reference/unsupported-boundary.mdx` are synchronized. `concepts/verification.mdx` does not exist on the target branch. Separate framework-free, standard-CI and all-framework test output is committed alongside the static-check logs.

Local gate results: **640 passed / 59 skipped** in the framework-free `.[dev]` unit gate; **682 passed / 47 skipped / 6 deselected** in the standard `.[dev,llm,cli]` suite; **771 passed / 1 skipped / 13 deselected** in the all-framework suite. Mypy (59 source files), Ruff, all five import contracts and whitespace checks passed. [Exact commands and exit codes](gate-commands.json) accompany the raw `gate-*.txt` logs. Existing opt-in and optional-dependency gates were not changed. OpenTelemetry settings stayed untouched during tests.

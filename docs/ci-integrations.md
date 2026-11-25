# CI integrations: scan and publish from customer pipelines

Maintainer reference for the integrations that run `donkey publish` in a
customer's CI pipeline (BG §2.5). This is separate from [`ci.md`](ci.md), which
describes the SDK repository's own build and test workflows.

Customer setup and copy-paste examples live in
[Scan & publish](https://docs.donkey-kit.dev/publishing). Each packaged
integration's README owns its inputs and usage. Keep those documents current
with the implementation; this page records ownership, packaging, release and
verification requirements rather than duplicating their examples.

## Integration map

| Integration | Source | Status |
| --- | --- | --- |
| GitHub Actions composite action | [`action/`](../action/README.md) in this repository | Shipped. Installs donkey-kit from the selected action ref into its own virtual environment. |
| GitLab CI/CD component | [`donkey-development-kit/donkey-publish`](https://gitlab.com/donkey-development-kit/donkey-publish) | Shipped in the GitLab CI/CD Catalog; the website documents release `1.0.0` with donkey-kit `0.2.1`. |
| Plain GitLab and Bitbucket jobs | [Website publishing guide](../website/content/publishing/ci.mdx), sections "From GitLab CI" and "From Bitbucket Pipelines" | Documented. Install a pinned published donkey-kit and invoke the CLI directly. |
| Bitbucket Pipe | [`donkey-development-kit/donkey-publish-pipe`](https://bitbucket.org/donkey-development-kit/donkey-publish-pipe/) | Image `0.1.0` published and signed, with donkey-kit `0.2.1`; consumer dry run verified in pipeline #7. SDK-update schedule activation remains open under [#1265](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/1265). |

The Donkey Development Kit maintainers own these integrations. An external
integration repository has its own releases; an SDK release does not by itself
update the component or Pipe customers have pinned.

## Shared execution contract

- Plan on pull or merge requests with `--dry-run`; publish from the trusted
  default branch. A dry run needs no control-plane credentials when
  `group_id` is declared in `[donkey.publication]`.
- Consume the CLI's versioned JSON: `plan_format` for a dry run and
  `publish_format` for a publish. A wrapper must reject an unsupported format
  rather than report success from a document it cannot interpret.
- Preserve the CLI's failure status and show its error lines. Partial
  publication is not success; a rerun can finish the incomplete publication.
- Keep the JSON result as a pipeline artifact, including failed runs where
  the CI platform supports it. Treat the result and log content as potentially
  sensitive customer metadata, not as permission to disclose credentials.
- Pin the integration to a full release version or commit SHA and the
  installed SDK to the version tested for that release.

The CLI implementation in [`publish.py`](../python/src/donkey_kit/cli/publish.py)
owns publication semantics. Platform wrappers add logs, outputs and artifacts,
not a second publication implementation. The generated Exchange Home page's
commit and run links currently come from GitHub Actions only; GitLab and
Bitbucket omit those links.

## Credentials and boundaries

| Credential | Used by | Storage and permissions |
| --- | --- | --- |
| `ANYPOINT_CLIENT_ID`, `ANYPOINT_CLIENT_SECRET`, `ANYPOINT_ORG_ID` | A customer's real publish job | CI secrets restricted to trusted publish jobs. The connected app needs `manage:exchange` on the target business group; see [`scopes.md`](scopes.md). Do not give these to untrusted PR jobs. |
| Bitbucket Git credential | Maintainers uploading the Pipe source | Local credential manager or SSH. It is not required by consumers of a public Pipe. |
| Docker Hub publishing token | The Pipe's image release pipeline | Secured pipeline variable, restricted to the image repository where available. Consumers of a public image do not need this token. |
| `COSIGN_PRIVATE_KEY_B64`, `COSIGN_PASSWORD` | The Pipe's release pipeline | Secured variables containing the Base64-encoded encrypted key and its password. Only `cosign.pub` is committed. |

Never put token values in source, command-line arguments, remote URLs, build
arguments, image layers or logs. A public source repository does not make a
published container image public automatically: verify anonymous pulls before
advertising the Pipe.

## Bitbucket Pipe packaging and release

Docker Hub is the selected image registry for #1265, with image repository
`tommyilpazzo/donkey-publish-pipe`. Release `0.1.0` is publicly available at
digest `sha256:62850dc16c2cc1d543ff5f829552f22133c5e1c0a5d46cb6be49cc725a8293d0`.
The matching Bitbucket tag points to commit `4b11a63` and must not be moved.

A Bitbucket Pipe runs as a Docker container: `pipe.yml` identifies its image,
the `Dockerfile` installs the pinned SDK, and the entrypoint invokes
`donkey publish`. Docker Hub stores the image; Bitbucket stores the source.
Customers need neither a local Docker installation nor registry credentials
to use a public image from Bitbucket Pipelines.

The implementation uses `SCAN_PATH`, `DRY_RUN`, `IF_CHANGED`,
`RESULT_FILE`, `DEBUG` and the `ANYPOINT_*` variables. `SCAN_PATH` deliberately
avoids replacing the container's executable-search `PATH`. Its default result
file is `donkey-publish.json`. Pipe `0.1.0` pins donkey-kit `0.2.1`.

Release checklist:

1. Verify the configured Docker Hub publishing credentials and repository
   coordinates without exposing the secured token.
2. Keep the digest-pinned base and hash-locked dependency set current. A pinned
   base and SDK version alone do not fix transitive dependency resolution;
   review source-package build dependencies too.
3. Run wrapper tests and container tests, then run a fixture dry run in real
   Bitbucket Pipelines. Keep its JSON artifact and pipeline link as evidence.
4. Build and publish the versioned image, record its digest, sign it, and
   verify both the signature and anonymous image pull. Keep source tags,
   `pipe.yml` and image versions consistent; do not overwrite a released tag.
5. Add the verified Pipe example to the website and its README, then update
   this page's status and evidence.

For subsequent SDK releases, update the SDK pin and dependency lock, repeat
the verification, and release a new Pipe version. Signing is implemented in
`scripts/release.py` in the Pipe repository, using a digest-pinned cosign tool.
The `sdk-update` custom pipeline prepares the latest stable PyPI SDK pin,
regenerates the lock, and rebuilds/tests a candidate without publishing. Review
its `requirements.in`, `requirements.lock` and `Dockerfile` artifacts on an
update branch, then choose a new Pipe version and use the signed tag release
pipeline. A daily Bitbucket schedule on the trusted default branch remains to
be activated; the custom pipeline alone does not enable recurring runs.
[Pipeline #9](https://bitbucket.org/donkey-development-kit/donkey-publish-pipe/pipelines/results/9)
successfully ran this custom pipeline with the current SDK `0.2.1`, including
lock generation, candidate image rebuild and tests. A future-version update
is covered by offline tests, not yet by a real newer PyPI release.

## Verification and troubleshooting

The GitHub action's wrapper tests live in
[`test_action.py`](../python/tests/unit/test_action.py). The SDK CI's
`action-self-test` job runs a fixture dry run on Linux, macOS and Windows; see
[`ci.yml`](../.github/workflows/ci.yml). This proves scan and plan behavior,
not a live Exchange publish.

For the Bitbucket Pipe, the
[#1265 progress report](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/1265#issuecomment-6867770186)
records six passing local tests, including a container dry run, and a missing
credential failure with exit code `2`. This is historical local evidence, not
a real Bitbucket pipeline result. Subsequently,
[Bitbucket pipeline #3](https://bitbucket.org/donkey-development-kit/donkey-publish-pipe/pipelines/results/3)
passed the wrapper tests, fixture dry run and container build/test at commit
`087f40b`. Runtime v2 supplies the Docker CLI and restricts bind mounts to the
checkout, so the container test uses a checkout-local temporary directory.
[Release pipeline #5, attempt 3](https://bitbucket.org/donkey-development-kit/donkey-publish-pipe/pipelines/results/5)
published and verified the signed `0.1.0` image. An independent cosign check
validated its claims, public key signature and transparency-log evidence;
an anonymous registry request returned HTTP 200 for the same digest.
[Pipeline #6](https://bitbucket.org/donkey-development-kit/donkey-publish-pipe/pipelines/results/6)
passed wrapper and container tests but failed the released consumer invocation.
Its downloaded log confirms the released digest pulled successfully, followed
by `PermissionError` while overwriting `donkey-publish.json`, an artifact
downloaded from an earlier step. The consumer test now writes and validates
its own `consumer-plan.json`.
[Pipeline #7](https://bitbucket.org/donkey-development-kit/donkey-publish-pipe/pipelines/results/7)
passed all three steps at commit `66faf2c`, including the released consumer
Pipe invocation and assertions on its freshly written plan format and asset ID.
This verifies consumer scan/plan execution, not a live Exchange publication.

| Symptom | Check |
| --- | --- |
| Image cannot be pulled | Confirm the image exists at the advertised version, is public and supports the runner's architecture. Public Bitbucket source alone is insufficient. |
| Dry run asks for an org ID | Declare `group_id` in `[donkey.publication]`; do not expose publish credentials to PRs to fix a plan. |
| Publish exits `2` | Check the scan path, configuration and presence of control-plane variables in the trusted publish step. |
| An asset is `failed` or `incomplete` | Read the CLI's detail and stderr; check the connected app's scope, fix the refusal and rerun with the same asset version. See [When a publish fails](https://docs.donkey-kit.dev/publishing#when-a-publish-fails). |
| Wrapper rejects the JSON format | Check the wrapper release and its SDK pin together. Do not suppress the format check. |
| Registry push is denied | Check the registry namespace and publishing token permissions, not the unrelated Anypoint credentials. |

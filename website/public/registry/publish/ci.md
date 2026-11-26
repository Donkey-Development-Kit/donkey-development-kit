# CI pipelines

Status: Live. Shipped, and verified against a real governed gateway.

Each recipe runs `donkey publish` in your pipeline. The plan, the JSON artifact
and the exit code behave the same on every provider. The overview of what
publishing does is on [Scan & publish](https://docs.donkey-kit.dev/registry/publish.md).

## From GitHub Actions

The scan-and-publish action runs `donkey publish` in a workflow. It lives in
the SDK repository's `action/` folder, so you pin it like any action, to a
release tag or a commit SHA. The action at a ref installs donkey-kit from the
same ref, into its own virtual environment. Your job's Python is left as it
was.

```yaml
# .github/workflows/publish.yml
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: Donkey-Development-Kit/donkey-development-kit/action@<ref>
        with:
          # On a pull request: plan only, with no credentials.
          dry-run: ${{ github.event_name == 'pull_request' }}
          client-id: ${{ secrets.ANYPOINT_CLIENT_ID }}
          client-secret: ${{ secrets.ANYPOINT_CLIENT_SECRET }}
          org-id: ${{ secrets.ANYPOINT_ORG_ID }}
```

A dry run needs no credentials, so it works on pull requests from forks, which
get no secrets. Set `group_id` in `[donkey.publication]` so the plan doesn't
need the org ID either. The action passes the credentials to `donkey` only
through the environment. A credential input left empty keeps the `ANYPOINT_*`
variable your job already sets.

Pass `client-secret` from a `secrets.*` value only. GitHub masks secrets before
any step runs. The action masks the input in its first step, but the runner
prints that step's environment before the mask takes effect, so a value from a
variable or another step's output shows in the log once.

| Input | Default | |
| --- | --- | --- |
| `path` | `.` | The directory (or one `.py` file) to scan and publish. |
| `dry-run` | `false` | `true` plans and sends nothing (`--dry-run`). |
| `if-changed` | `true` | `false` publishes even when the digest matches (`--always`). |
| `client-id`, `client-secret`, `org-id` | empty | `ANYPOINT_CLIENT_ID`, `ANYPOINT_CLIENT_SECRET`, `ANYPOINT_ORG_ID`. |
| `python-version` | `3.12` | The Python that runs donkey-kit (3.10 or later). |
| `donkey-kit` | empty | A pip requirement to install instead, such as `donkey-kit[cli]==X.Y.Z`. |
| `constraints` | empty | A pip constraints file in your repository, which pins the versions donkey-kit's dependencies install at (`pip install -c`). |

By default the action installs the newest version of each dependency that
donkey-kit allows, so a new release of one of them reaches your pipeline the
day it ships. To keep publishing reproducible, pin `donkey-kit` to a release
and commit a constraints file for the job's Python, then pass its path as
`constraints`. One way to write it is to run
`pip freeze --exclude donkey-kit > constraints/publish.txt` in a clean virtual
environment where you installed `donkey-kit[cli]==X.Y.Z`, with the same
Python as `python-version`. You refresh the file yourself, when you choose to.

The job summary shows a table of the assets: each one's digest on a dry run,
and each one's status and detail after a publish, including one that failed
partway. The step's outputs are the `published`, `unchanged`, `incomplete`,
`failed` and `not-attempted` counts, and `result`, the path of the
JSON that `donkey publish --json` printed. A failed publish fails the step with
`donkey`'s exit code, and each line `donkey` printed to stderr becomes an error
annotation, so the fix shows on the run's page. Re-running the failed job
finishes the publish (see [When a publish fails](https://docs.donkey-kit.dev/registry/publish.md#when-a-publish-fails)).
What `donkey` prints comes from the repository it scans, so the summary shows
every field as plain text, and the stderr echo can't run a workflow command.

The action reads only the document of its own release: `plan_format: 1` on a
dry run, `publish_format: 1` otherwise (see [JSON output](https://docs.donkey-kit.dev/cli.md#json-output)).
If the `donkey-kit` input installs a release that prints another, the action
writes no summary and no outputs. It fails the step with an annotation that
names the format it found, even when the publish succeeded.

## From GitLab CI

The `donkey-publish` component runs `donkey publish` in a GitLab pipeline. It
is listed in the GitLab CI/CD Catalog, and you include it pinned to a release:

```yaml
# .gitlab-ci.yml
include:
  - component: $CI_SERVER_FQDN/donkey-development-kit/donkey-publish/publish@1.0.0
```

That adds a `donkey-publish` job which plans on merge requests and publishes
from the default branch. Pin a full version or a commit SHA: `~latest` and a
partial version like `1` move with the component, not with your review. The
component's source and changelog are in its
[GitLab project](https://gitlab.com/donkey-development-kit/donkey-publish).

Add `ANYPOINT_CLIENT_ID`, `ANYPOINT_CLIENT_SECRET` and `ANYPOINT_ORG_ID` as
CI/CD variables under **Settings > CI/CD > Variables**. Mask the secret and
protect all three: a protected variable reaches only pipelines on protected
branches, so the default branch gets the credentials and a merge request
doesn't. They are variables rather than inputs, because GitLab writes an
input into the pipeline configuration. The dry run needs none; set `group_id`
in `[donkey.publication]` so it doesn't need the org ID either.

| Input | Default | |
| --- | --- | --- |
| `stage` | `test` | The stage the `donkey-publish` job runs in. |
| `path` | `.` | The directory (or one `.py` file) to scan and publish. |
| `dry-run` | `auto` | `auto` plans in merge request pipelines and publishes on the default branch. `true` always plans; `false` always publishes. |
| `if-changed` | `true` | `false` publishes even when the digest matches (`--always`). |
| `python-version` | `3.12` | Selects the `python:<version>-slim` image (3.10 or later). |
| `donkey-kit` | the release's pin | The pip requirement to install. Each component release pins the donkey-kit it was tested with (`donkey-kit[cli]==0.2.1` in 1.0.0). |
| `constraints` | empty | A pip constraints file in your repository (`pip install -c`), as for the [GitHub action](#from-github-actions). |
| `artifact-expire-in` | `30 days` | How long GitLab keeps the artifacts. |

The job keeps `donkey-publish.json`, the JSON that `donkey publish --json`
printed, and `donkey-publish-summary.txt`, the table of assets and issues it
also prints in the log. Both are kept even when the job fails. A dotenv report
gives jobs that `needs:` this one the `DONKEY_PUBLISHED`, `DONKEY_UNCHANGED`,
`DONKEY_INCOMPLETE`, `DONKEY_FAILED` and `DONKEY_NOT_ATTEMPTED` counts. A
failed publish fails the job with `donkey`'s exit code, and re-running the job
finishes the publish (see [When a publish fails](https://docs.donkey-kit.dev/registry/publish.md#when-a-publish-fails)). Like
the action, the component reads only the JSON format of the donkey-kit it
pins, and drops control characters from what `donkey` prints.

On a self-managed GitLab instance, which can't include a component from
gitlab.com, run
`donkey publish` in a job of your own:

```yaml
# .gitlab-ci.yml
donkey-publish:
  image: python:3.12-slim
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
      variables:
        DONKEY_ARGS: "--dry-run"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
      variables:
        DONKEY_ARGS: ""
  script:
    - pip install "donkey-kit[cli]==X.Y.Z"
    - donkey publish . $DONKEY_ARGS --json > donkey-publish.json
  artifacts:
    when: always
    paths:
      - donkey-publish.json
```

`donkey publish` prints the JSON on stdout, which the job keeps as the
artifact. Any issue or refusal goes to stderr, so it shows in the job log, and
the exit code fails the job.

## From Bitbucket Pipelines

The [donkey-publish-pipe](https://bitbucket.org/donkey-development-kit/donkey-publish-pipe/)
packages the CLI for Bitbucket. Release `0.1.0` runs donkey-kit `0.2.1` in a
public, signed Docker Hub image. Pin a full Pipe version:

```yaml
pipelines:
  pull-requests:
    "**":
      - step:
          name: Plan publication
          script:
            - pipe: donkey-development-kit/donkey-publish-pipe:0.1.0
              variables:
                DRY_RUN: "true"
          artifacts:
            - donkey-publish.json
  branches:
    main:
      - step:
          name: Publish to Exchange
          deployment: production
          script:
            - pipe: donkey-development-kit/donkey-publish-pipe:0.1.0
              variables:
                ANYPOINT_CLIENT_ID: $ANYPOINT_CLIENT_ID
                ANYPOINT_CLIENT_SECRET: $ANYPOINT_CLIENT_SECRET
                ANYPOINT_ORG_ID: $ANYPOINT_ORG_ID
          artifacts:
            - donkey-publish.json
```

The Pipe accepts `SCAN_PATH` (default `.`), `DRY_RUN` (default `false`),
`IF_CHANGED` (default `true`; `false` selects `--always`) and `RESULT_FILE`
(default `donkey-publish.json`). Its README documents the variables and
signature verification key. You need no Docker Hub account to use it.

Alternatively, install the CLI in a step of your own. This one plans on pull
requests and publishes from `main`:

```yaml
# bitbucket-pipelines.yml
image: python:3.12-slim

definitions:
  steps:
    - step: &donkey-plan
        name: donkey publish (plan)
        script:
          - pip install "donkey-kit[cli]==X.Y.Z"
          - donkey publish . --dry-run --json > donkey-publish.json
        artifacts:
          - donkey-publish.json

pipelines:
  pull-requests:
    "**":
      - step: *donkey-plan
  branches:
    main:
      - step:
          name: donkey publish
          deployment: production
          script:
            - pip install "donkey-kit[cli]==X.Y.Z"
            - donkey publish . --json > donkey-publish.json
          artifacts:
            - donkey-publish.json
```

Add `ANYPOINT_CLIENT_ID`, `ANYPOINT_CLIENT_SECRET` and `ANYPOINT_ORG_ID` as
secured variables of the `production` deployment environment under
**Repository settings > Deployments**. Deployment variables reach only steps
that name that environment, so the `main` step gets the credentials and the
pull-request step doesn't. The dry run needs none; set `group_id` in
`[donkey.publication]` so it doesn't need the org ID either.

As on GitLab, the JSON goes to the artifact, any issue or refusal to the step
log, and the exit code fails the step.

On GitLab and Bitbucket, the generated Home page leaves out the commit and
run links: `donkey publish` reads them only from GitHub Actions.

## Next steps

- [Scan & publish](https://docs.donkey-kit.dev/registry/publish.md): the scan, plan and publish flow these pipelines run.
- [Publication & verify()](https://docs.donkey-kit.dev/registry/publish/publication.md): the runtime check that a published version still matches.

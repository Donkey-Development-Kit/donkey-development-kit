# Donkey scan and publish

A GitHub Action that runs `donkey publish`. It scans a repository's MCP servers
and agents and publishes the ones that changed to Anypoint Exchange (#219,
#1260).

```yaml
- uses: Donkey-Development-Kit/donkey-development-kit/action@<ref>  # a release tag or a commit SHA
  with:
    dry-run: ${{ github.event_name == 'pull_request' }}
    client-id: ${{ secrets.ANYPOINT_CLIENT_ID }}
    client-secret: ${{ secrets.ANYPOINT_CLIENT_SECRET }}
    org-id: ${{ secrets.ANYPOINT_ORG_ID }}
```

When a publish fails partway, the job summary and the outputs still list every
asset with its status (`failed`, `incomplete`, `not_attempted`), and re-running
the job finishes what was left. The inputs, outputs and failure behaviour are
documented in
[Scan & publish → From GitHub Actions](https://docs.donkey-kit.dev/publishing#from-github-actions).

| File | |
| --- | --- |
| `action.yml` | The composite action. It installs donkey-kit from this checkout of the repository into its own virtual environment, then runs `run.py`. |
| `run.py` | Runs `donkey --json publish` and writes the job summary, the step outputs and the error annotations, from the JSON donkey prints on a failed publish too. It reads only `plan_format` 1 (a dry run) and `publish_format` 1, and fails the step on any other document. Standard library only. Tested by `python/tests/unit/test_action.py`. |
| `tests/fixture/` | One MCP server, which `.github/workflows/action-self-test.yml` dry-runs on Linux, macOS and Windows. |

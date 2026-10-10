# Release notes

The release notes for every `donkey-kit` version are its
[GitHub Release](https://github.com/Donkey-Development-Kit/donkey-development-kit/releases).
There is no separate changelog file, so the Release is the one place to read
what changed. The **Changelog** link on
[PyPI](https://pypi.org/project/donkey-kit/) points at the same page.

Each Release covers:

- **Breaking changes.** Every Release has this section. It lists each break
  with its migration, or says `None.`.
- **Verification-status changes**, for example an adapter that moved from
  signature-confirmed to live-verified.
- **Extras changes**, such as a new framework floor.
- The pull requests in the release, grouped as features, fixes, docs and
  maintenance.

To read the notes for one version, open
`https://github.com/Donkey-Development-Kit/donkey-development-kit/releases/tag/v<version>`,
for example
[`v0.1.2`](https://github.com/Donkey-Development-Kit/donkey-development-kit/releases/tag/v0.1.2).

## Which version you have

```bash
python -c "import donkey_kit; print(donkey_kit.__version__)"
pip index versions donkey-kit      # the versions published on PyPI
```

The banner at the top of each page shows which version these docs describe.
When it differs from the latest release on PyPI, the banner shows both. The
site is built from the released branch, so it describes the latest release
unless a newer one was published after the last build.

  The [Python API reference](https://docs.donkey-kit.dev/reference/api.md) is generated from the code of the
  version these docs describe. For another version, run `help()` on the object
  in your own environment.

## What's planned

The [Roadmap](https://docs.donkey-kit.dev/roadmap.md) lists the next milestones and their issues.

# Security policy

<!-- toc -->
**Contents**

- [Supported versions](#supported-versions)
- [Reporting a vulnerability](#reporting-a-vulnerability)
  - [What is in scope](#what-is-in-scope)
- [What to expect](#what-to-expect)
- [Disclosure process](#disclosure-process)
<!-- tocstop -->

## Supported versions

`donkey-kit` is pre-1.0. Security fixes land on `develop` and ship in the next
release of the **latest minor line**; older lines get no backports.

| Version | Supported |
| --- | --- |
| 0.1.x (latest release) | Yes |
| Anything older, and `.devN` / `rcN` pre-releases once the final is out | No |

## Reporting a vulnerability

**Do not open a public issue, pull request or discussion for a vulnerability.**

Report it privately through GitHub's private vulnerability reporting:

1. Go to the repository's [**Security** tab](https://github.com/Donkey-Development-Kit/donkey-development-kit/security).
2. Choose **Report a vulnerability**
   ([direct link](https://github.com/Donkey-Development-Kit/donkey-development-kit/security/advisories/new)).

Please include:

- the affected version (`python -c "import donkey_kit; print(donkey_kit.__version__)"`)
  and the installed extras;
- what an attacker can do, and the steps or a minimal script to reproduce it;
- any logs or captures, **with credentials, tokens and tenant identifiers
  removed**.

### What is in scope

The code in this repository: the `donkey-kit` Python package (`python/`), its
CI and release workflows, and the docs site (`website/`).

Out of scope: vulnerabilities in the MuleSoft / Salesforce platform itself
(Agent Fabric, Omni Gateway, Anypoint). This is an independent, community
project (see the [support statement](README.md#support--trademark-statement-please-read));
report platform issues to Salesforce through its own security disclosure
channel. Issues in a third-party framework (LangChain, CrewAI and others) go to
that project, unless the SDK's adapter is what makes them exploitable.

## What to expect

The maintainers handle reports on a best-effort basis, without an SLA. As
targets:

- an acknowledgement within **5 business days**;
- a first assessment (accepted, needs more information, or declined, with the
  reason) within **10 business days**;
- updates on the private advisory at least every two weeks until it is resolved.

## Disclosure process

1. The report is triaged in a private GitHub security advisory. You're added to
   it and can follow the work there.
2. The fix is developed in the advisory's temporary private fork, then merged
   and released as a new patch version on PyPI.
3. The advisory is published, with a CVE requested through GitHub when the
   issue warrants one, and the release notes link to it. You're credited unless
   you ask not to be.

We ask that you keep the details private until the advisory is published, or
until 90 days after the report if no fix has shipped by then, whichever comes
first. If you need a different timeline, say so in the report.

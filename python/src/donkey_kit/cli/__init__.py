"""The ``donkey`` CLI: ``init``, ``doctor``, ``mock``, ``dev``, ``test``, ``scan``,
``publish``, ``verify``, ``expose`` and ``preflight``.

The console script is ``donkey = "donkey_kit.cli:main"``. Each command lives in
its own module (:mod:`.init`, :mod:`.doctor`, :mod:`.mock`, :mod:`.dev`, :mod:`.test`,
:mod:`.scan`, :mod:`.publish`, :mod:`.verify`, :mod:`.expose`) and
registers on the shared app in :mod:`._app`. The CLI is a front end over the
library: it may import any layer, and no layer imports it (an import-linter
contract enforces that). Why it lives here, and why the old ``provisioning/``
package and ``governance.py`` were deleted rather than kept, is ADR 0008 in
``docs/adr/`` (legacy quarantine and the CLI's home, #730).

Telemetry is on by default (BG §1.6), but export stays inert unless an
OTLP endpoint is configured: set ``OTEL_EXPORTER_OTLP_ENDPOINT`` and spans flow
to your own sink with no SDK-specific env var; opt out entirely with
``DONKEY_TELEMETRY=false``.

``publish`` (#218, #1291) registers a repository's MCP servers and agents in
Exchange from CI, and ``verify`` (#1352) checks each published MCP server
against its live tools. ``expose`` (#226) puts one of them behind an existing
gateway: its Exchange asset, API Manager instance, policies and deployment;
``preflight`` (#1279) checks the same read-only. One command is hidden:
``status`` (BG §2.5) is verification-blocked: it prints a
"blocked on verification" message and exit 3 rather than fabricating calls
(§0.3). The refused provisioning control plane
(``plan``/``apply``/``drift``/``lint``/``generate``/``validate``) was removed.

Needs the ``[cli]`` extra; importing this package without it raises an
``ImportError`` naming the install command.
"""

from __future__ import annotations

# _app first: its curated ImportError must fire before a command module's own
# `import typer` raises a bare one. The command modules are then imported for
# their registration side effect.
from ._app import app, main  # isort: skip
from . import _blocked, dev, doctor, expose, init, mock, publish, scan, test, verify  # noqa: F401  # isort: skip

__all__ = ["app", "main"]

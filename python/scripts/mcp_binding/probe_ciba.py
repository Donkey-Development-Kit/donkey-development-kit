#!/usr/bin/env python3
"""How each framework's MCP class surfaces the OBO policy's CIBA step-up on ``tools/call`` (#214).

The live CIBA captures (ledger §4, #1320) are on an A2A ``message/send``, so what a framework
does with the same ``401`` ``authorization_pending`` or ``403`` ``access_denied`` on an MCP
``tools/call`` is not known. This starts the local server with ``--ciba add=pending`` and
``--ciba add=denied`` and calls ``add`` through each framework installed in this interpreter,
the way :mod:`probe_denial` does. It records whether the call raised (the exception chain's
types, the HTTP status where one rides along, the JSON-RPC ``code``) or returned, and whether
the CIBA body (``authorization_pending`` / ``access_denied``) is still visible in the text.

Usage (throwaway venv with the frameworks and mcp installed), from python/:
    python scripts/mcp_binding/probe_ciba.py [--write --name VENV] [--only NAME ...]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from typing import Any

from _harness import distributions, local_mcp_server, write_fixture
from probe_denial import PROBES, _installed

MODES = {"pending": "authorization_pending", "denied": "access_denied"}


def _visible(observed: Any, marker: str) -> bool:
    """Whether the CIBA ``error`` string survives anywhere in what the framework gave back."""
    return marker in json.dumps(observed, default=repr)


def main() -> None:
    """Run every installed framework against each CIBA mode; with ``--write`` record it."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--name", default="ciba", help="fixture name (ciba-<name>.json)")
    args = parser.parse_args()
    names = [n for n in PROBES if (args.only is None or n in args.only)]
    observed: dict[str, dict[str, Any]] = {}
    dists: tuple[str, ...] = ()
    for mode, marker in MODES.items():
        with local_mcp_server(ciba=f"add={mode}") as srv:
            for name in names:
                module, probe, needs = PROBES[name]
                if not _installed(module):
                    continue
                result = asyncio.run(probe(srv.url))
                result["body_visible"] = _visible(result, marker)
                observed.setdefault(name, {})[mode] = result
                dists += needs
    fixture = {
        "probe": "probe_ciba.py",
        "recorded": dt.date.today().isoformat(),
        "distributions": distributions(*dists),
        "python": sys.version.split()[0],
        "ciba": "HTTP 401 authorization_pending (Retry-After) / 403 access_denied on tools/call",
        "observed": observed,
    }
    print(json.dumps(fixture, indent=2, sort_keys=True, default=repr))
    if args.write:
        print(write_fixture(f"ciba-{args.name}", fixture), file=sys.stderr)


if __name__ == "__main__":
    main()

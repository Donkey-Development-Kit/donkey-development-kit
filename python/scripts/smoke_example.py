#!/usr/bin/env python3
"""Strict smoke for one framework example (#748).

Run by hand, an example prints install guidance and exits 0 when its framework
is missing. That is right for a user and wrong for CI, where a broken install
would read as a pass. This script is the CI form of the same example: it loads
``examples/<example>/main.py`` by path and calls its real ``build(donkey)``, so
a missing framework or a drifted constructor signature raises and the process
exits non-zero. It then drives ``build`` through the public conformance kit,
which makes governed calls in-process against the captured gateway fixtures: no
network, no credentials, no simulator. A scenario the example lists in
``KNOWN_LIMITATIONS`` is reported as an asserted exemption, not run (CrewAI owns
its transport, so its smoke is construction only).

The examples themselves stay free of CI scaffolding.

Usage:
    python scripts/smoke_example.py adk
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from donkey_kit import Donkey
from donkey_kit.conformance.harness import offline_config, run_conformance

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def load_example(example: str) -> ModuleType:
    """Import ``examples/<example>/main.py`` by path; ``examples`` is not a package."""
    name = f"ddk_smoke_example_{example}"
    spec = importlib.util.spec_from_file_location(name, _EXAMPLES / example / "main.py")
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"no example at {_EXAMPLES / example / 'main.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


async def smoke(example: str) -> list[str]:
    """Build the example's native object, then run the conformance kit against it.

    Returns the failed scenarios as ``"<scenario>: <detail>"``. A missing
    framework raises ``ImportError`` from ``build``."""
    module = load_example(example)
    donkey = Donkey(offline_config())
    try:
        module.build(donkey)
    finally:
        await donkey.aclose()
    known = getattr(module, "KNOWN_LIMITATIONS", None)
    results = await run_conformance(module.build, known_limitations=known)
    return [f"{r.scenario}: {r.detail}" for r in results if r.status == "fail"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("example", help="directory under examples/, e.g. adk or openai_agents")
    args = parser.parse_args(argv)
    failed = asyncio.run(smoke(args.example))
    if failed:
        print(f"{args.example}: conformance scenario(s) failed:", file=sys.stderr)
        for line in failed:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"{args.example}: built the native object and passed the conformance kit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

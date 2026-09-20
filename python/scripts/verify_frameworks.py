#!/usr/bin/env python3
"""Live-verify the framework constructor signatures (docs/verified-apis.md §8).

This is the executable form of the verification discipline step for Pillar 1's adapters.
It answers one question per framework: **does the exact class we name actually
exist, and does it accept the exact kwargs we pass?** — without inventing
anything. Nothing here is asserted as fact until this script confirms it against
the *installed* framework package (and, with ``--live``, the real proxy).

Two independent checks per framework:

  A. SIGNATURE (offline, needs only the framework installed):
     import the native class via the adapter's factory and construct it. If the
     class path is wrong -> ImportError/AttributeError. If a kwarg name is wrong
     -> TypeError. Construction succeeding *is* the signature verification. The
     script then confirms the object's real ``module.ClassName`` matches the
     value recorded in docs/verified-apis.md §8, so a silently-renamed class is caught too.

  B. LIVE ROUND-TRIP (``--live``, needs the 3 DONKEY_LLM_PROXY_* env vars):
     make one real completion through the framework's *own* native call and
     confirm a governed response comes back. Only LangGraph's call API is
     exercised directly here (``ChatOpenAI.ainvoke``); for the others the
     framework's agent-loop/runtime API is itself unverified, so the script
     constructs the object and then probes the proxy path via the framework's
     underlying OpenAI client only where that is safe — otherwise it records
     LIVE as ``skipped (framework runtime API not verified)`` rather than
     guessing a method name.

Usage:
    python scripts/verify_frameworks.py                # signature check, all installed
    python scripts/verify_frameworks.py --live         # + one real proxy round-trip
    python scripts/verify_frameworks.py --only langgraph strands
    python scripts/verify_frameworks.py --emit-verified
        # print docs/verified-apis.md §8 markdown rows to paste
    python scripts/verify_frameworks.py --json

Exit code is non-zero if any *installed* framework fails its signature check, so
this doubles as a CI gate (see .github/workflows/nightly-matrix.yml).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from dataclasses import asdict, dataclass, field

# --- ground truth: the exact docs/verified-apis.md §8 rows this script confirms ---
# (framework key, factory import path, factory fn, expected native module.Class)
# This stays the FULL eight-framework roster so the offline signature check can be
# run on demand for any of them. Only LangGraph is conformance-tested and carried
# in the nightly matrix (BG §1.8, #197); the other seven are supported at
# connection_kwargs() only. `--only <fw>` targets one framework.
FRAMEWORKS: list[tuple[str, str, str, str]] = [
    ("langgraph", "donkey_kit.integrations.langgraph", "chat_model",
     "langchain_openai.ChatOpenAI"),
    ("adk", "donkey_kit.integrations.adk", "model",
     "google.adk.models.lite_llm.LiteLlm"),
    ("strands", "donkey_kit.integrations.strands", "model",
     "strands.models.openai.OpenAIModel"),
    ("agent_framework", "donkey_kit.integrations.agent_framework", "chat_client",
     "agent_framework.openai.OpenAIChatClient"),
    ("openai_agents", "donkey_kit.integrations.openai_agents", "model",
     "agents.OpenAIChatCompletionsModel"),
    ("anthropic", "donkey_kit.integrations.anthropic", "client",
     "anthropic.AsyncAnthropic"),
    ("crewai", "donkey_kit.integrations.crewai", "llm",
     "crewai.LLM"),
    ("llamaindex", "donkey_kit.integrations.llamaindex", "llm",
     "llama_index.llms.openai_like.OpenAILike"),
]

PROXY_ENV = (
    "DONKEY_LLM_PROXY_URL",
    "DONKEY_LLM_PROXY_CLIENT_ID",
    "DONKEY_LLM_PROXY_CLIENT_SECRET",
)
MODEL = os.environ.get("DEMO_MODEL", "gpt-4o")


@dataclass
class Result:
    framework: str
    expected_class: str
    installed: bool = False
    blocked: bool = False  # adapter raised NotImplementedError (blocked on verification)
    signature_ok: bool | None = None  # None = not attempted
    actual_class: str | None = None
    class_matches: bool | None = None
    live: str = "not run"  # "ok" | "skipped: …" | "fail: …" | "not run"
    detail: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if not self.installed:
            return "NOT INSTALLED"
        if self.blocked:
            return "BLOCKED (verification discipline)"
        if self.signature_ok and self.class_matches:
            return "VERIFIED" if self.live in ("ok", "not run") else "SIGNATURE OK / LIVE FAIL"
        if self.signature_ok and self.class_matches is False:
            return "CLASS RENAMED"
        return "SIGNATURE FAIL"


def _ensure_proxy_env_for_offline() -> bool:
    """Signature checks call ``connection_kwargs()`` which validates proxy config.
    If real creds are absent, inject harmless placeholders so construction can be
    exercised offline. Returns True if REAL creds are present (live is possible)."""
    have_real = all(os.environ.get(v) for v in PROXY_ENV)
    if not have_real:
        os.environ.setdefault("DONKEY_LLM_PROXY_URL", "https://placeholder.invalid/proxy/")
        os.environ.setdefault("DONKEY_LLM_PROXY_CLIENT_ID", "placeholder-cid")
        os.environ.setdefault("DONKEY_LLM_PROXY_CLIENT_SECRET", "placeholder-secret")
    return have_real


def _actual_class(obj: object) -> str:
    return f"{type(obj).__module__}.{type(obj).__name__}"


def _import_expected(path: str) -> type | None:
    """Import the class named at ``path`` (e.g. ``langchain_openai.ChatOpenAI``)
    from its *public* recorded location. Returns None if the path does not
    resolve — which is itself a signature failure (the docs/verified-apis.md §8 name is wrong)."""
    import importlib

    module_path, _, attr = path.rpartition(".")
    try:
        mod = importlib.import_module(module_path)
        obj = getattr(mod, attr)
    except (ImportError, AttributeError):
        return None
    return obj if isinstance(obj, type) else None


def check_signature(res: Result, import_path: str, factory: str) -> object | None:
    """Check A — construct the native object via the adapter factory."""
    import importlib

    try:
        mod = importlib.import_module(import_path)
    except ImportError:
        res.installed = False
        return None

    fn = getattr(mod, factory)
    try:
        # Anthropic's native surface is a client; the model id is a per-call
        # argument, so its factory takes no positional model (BG §1.8 divergence).
        obj: object = fn() if res.framework == "anthropic" else fn(MODEL)
    except NotImplementedError as exc:  # blocked on verification
        res.installed = True
        res.blocked = True
        res.detail = str(exc).splitlines()[0]
        return None
    except ImportError:
        # The framework's own package isn't installed (factory imports it lazily).
        res.installed = False
        return None
    except TypeError as exc:  # a kwarg name/signature is WRONG — the key failure
        res.installed = True
        res.signature_ok = False
        res.detail = f"TypeError (kwarg/signature mismatch): {exc}"
        return None
    except Exception as exc:  # noqa: BLE001 - surface anything else verbatim
        res.installed = True
        res.signature_ok = False
        res.detail = f"{type(exc).__name__}: {exc}"
        return None

    res.installed = True
    res.signature_ok = True
    res.actual_class = _actual_class(obj)
    # Verify the docs/verified-apis.md §8 name by importing the class from its RECORDED public
    # path and checking the constructed object is an instance of it. This is robust to
    # re-exports (``langchain_openai.ChatOpenAI`` is defined in an internal
    # submodule but re-exported at the package top level, which is the path users
    # import and docs/verified-apis.md §8 records).
    expected_cls = _import_expected(res.expected_class)
    if expected_cls is None:
        res.class_matches = False
        res.notes.append(
            f"docs/verified-apis.md §8 path {res.expected_class!r} does not resolve to a class "
            "— fix docs/verified-apis.md §8"
        )
    else:
        res.class_matches = isinstance(obj, expected_cls)
        if not res.class_matches:
            res.notes.append(
                f"object is {res.actual_class!r}, not a {res.expected_class!r} "
                "— update docs/verified-apis.md §8"
            )
    return obj


async def check_live(res: Result, obj: object) -> None:
    """Check B — one real completion through the framework's native call.

    Only LangGraph's runtime call is verified here. For the rest, the framework's
    agent-loop API is itself unverified (verification discipline), so we do not guess a method — the
    proxy path they share is already live-verified via the raw client (docs/verified-apis.md §2).
    """
    if res.framework == "langgraph":
        try:
            reply = await obj.ainvoke([("user", "Say hi in exactly three words.")])  # type: ignore[attr-defined]
            text = getattr(reply, "content", str(reply))
            res.live = "ok"
            res.detail = f"completion: {text!r}"
        except Exception as exc:  # noqa: BLE001
            res.live = f"fail: {type(exc).__name__}: {exc}"
        return
    res.live = (
        "skipped: framework runtime call API not verified (docs/verified-apis.md §8/§9); "
        "shared proxy path verified via raw client (docs/verified-apis.md §2)"
    )


async def run(only: list[str] | None, live: bool) -> list[Result]:
    have_real = _ensure_proxy_env_for_offline()
    results: list[Result] = []
    for key, import_path, factory, expected in FRAMEWORKS:
        if only and key not in only:
            continue
        res = Result(framework=key, expected_class=expected)
        obj = check_signature(res, import_path, factory)
        if live and obj is not None and res.class_matches:
            if not have_real:
                res.live = (
                    "skipped: set the 3 DONKEY_LLM_PROXY_* env vars for a live round-trip"
                )
            else:
                await check_live(res, obj)
        results.append(res)
    return results


def print_table(results: list[Result]) -> None:
    w = max((len(r.framework) for r in results), default=9)
    header = (
        f"\n{'framework':<{w}}  {'installed':<9}  {'signature':<9}  "
        f"{'expected':<8}  {'live':<6}  verdict"
    )
    print(header)
    print("-" * (w + 55))
    for r in results:
        installed = "yes" if r.installed else "no"
        sig = "-" if r.signature_ok is None else ("ok" if r.signature_ok else "FAIL")
        cls = "-" if r.class_matches is None else ("ok" if r.class_matches else "RENAMED")
        live = r.live.split(":")[0]
        print(f"{r.framework:<{w}}  {installed:<9}  {sig:<9}  {cls:<8}  {live:<6}  {r.verdict}")
    print()
    for r in results:
        if r.detail or r.notes:
            print(f"• {r.framework}: {r.detail}")
            for n in r.notes:
                print(f"    ↳ {n}")


def emit_verified_rows(results: list[Result]) -> None:
    """Print docs/verified-apis.md §8 markdown rows for frameworks whose signature is now confirmed,
    so a maintainer can paste them into docs/verified-apis.md after sign-off."""
    from datetime import date  # local import; only used for this opt-in report

    today = date.today().isoformat()
    print("\n# Paste into docs/verified-apis.md §8 (maintainer sign-off still required):\n")
    for r in results:
        if r.signature_ok and r.class_matches:
            status = "VERIFIED (LIVE)" if r.live == "ok" else "VERIFIED (signature)"
            print(
                f"| {r.framework} | `{r.expected_class}` | {status} "
                f"| {r.actual_class} | {today} | verify_frameworks.py |"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="also make one real proxy round-trip")
    ap.add_argument("--only", nargs="+", metavar="FW", help="restrict to these framework keys")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument(
        "--emit-verified", action="store_true",
        help="print docs/verified-apis.md §8 markdown rows for confirmed frameworks",
    )
    args = ap.parse_args()

    try:
        results = asyncio.run(run(args.only, args.live))
    except Exception:  # noqa: BLE001 - never let the harness itself blow up silently
        traceback.print_exc()
        return 2

    if args.json:
        print(json.dumps([asdict(r) | {"verdict": r.verdict} for r in results], indent=2))
    else:
        print_table(results)
        if args.emit_verified:
            emit_verified_rows(results)

    # Fail CI only when an INSTALLED framework fails its signature check.
    failed = [
        r for r in results
        if r.installed and not r.blocked and not (r.signature_ok and r.class_matches)
    ]
    if failed:
        print(f"\n{len(failed)} installed framework(s) failed signature verification: "
              f"{', '.join(r.framework for r in failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

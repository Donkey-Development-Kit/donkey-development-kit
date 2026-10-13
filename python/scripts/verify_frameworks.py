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
     script then confirms the object is an instance of the class ``FRAMEWORKS``
     records (the docs/verified-apis.md §8 name), so a silently-renamed class is
     caught too.

  B. LIVE ROUND-TRIP (``--live``, needs the 3 DONKEY_LLM_PROXY_* env vars):
     run one real user turn through the framework's *own* smallest agent,
     built around the object check A constructed (LangGraph ``ainvoke``, ADK
     ``InMemoryRunner``, Strands ``Agent``, OpenAI Agents ``Runner.run``, CrewAI
     ``Crew.kickoff``, LlamaIndex ``FunctionAgent``, Agent Framework ``Agent``,
     Anthropic ``messages.create``), and confirm a non-empty reply comes back.
     ``agent_framework.responses`` and LangGraph's default call ``/responses``,
     so point the proxy at one that serves it. ADK's native Gemini model and the
     Anthropic client need their own wire format: they run only when
     ``DONKEY_GEMINI_PROXY_URL`` / ``DONKEY_ANTHROPIC_PROXY_URL`` is set.

Usage:
    python scripts/verify_frameworks.py                # signature check, all installed
    python scripts/verify_frameworks.py --live         # + one real agent turn per framework
    python scripts/verify_frameworks.py --only langgraph strands
    python scripts/verify_frameworks.py --only langgraph --require-installed
        # also fail if "langgraph" (or any --only key) is NOT INSTALLED
    python scripts/verify_frameworks.py --emit-verified
        # print docs/verified-apis.md §8 markdown rows to paste
    python scripts/verify_frameworks.py --json

Exit code is non-zero if any *installed* framework (its distribution is present)
fails its signature check, so this doubles as a CI gate (see
.github/workflows/nightly-matrix.yml). Without ``--only``, a framework that
simply is not installed is reported as "NOT INSTALLED" and exits 0 — that is
correct for an ad hoc, offline, "check whatever I have" run. But a CI leg that
installed one specific framework and calls ``--only <fw>`` has a different
failure mode to guard against: if that install silently broke (wrong extra
name, a resolver skip, …), the framework reads as NOT INSTALLED and the leg
still exits 0 (#748) — the exact "a broken install passes as absent" gap this
flag closes. ``--require-installed`` makes that case fail too: every key named
by ``--only`` must come back installed, not just signature-correct.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

# --- ground truth: the exact docs/verified-apis.md §8 rows this script confirms ---
# (framework key, factory import path, factory fn, expected native module.Class,
#  the distribution the framework's extra installs — "installed" means it is present)
# This stays the FULL eight-framework roster so the offline signature check can be
# run on demand for any of them. Only LangGraph is conformance-tested and carried
# in the nightly matrix (BG §1.8, #197); the other seven are supported at
# connection_kwargs() only. `--only <fw>` targets one framework.
FRAMEWORKS: list[tuple[str, str, str, str, str]] = [
    (
        "langgraph",
        "donkey_kit.integrations.langgraph",
        "chat_model",
        "langchain_openai.ChatOpenAI",
        "langchain-openai",
    ),
    (
        "adk",
        "donkey_kit.integrations.adk",
        "model",
        "google.adk.models.lite_llm.LiteLlm",
        "google-adk",
    ),
    # ADK's native Gemini on a Format=Gemini proxy (#691). A dotted key is a second
    # factory of the same framework: `--only adk` selects both, and its extra is
    # the part before the dot.
    (
        "adk.gemini",
        "donkey_kit.integrations.adk",
        "gemini",
        "google.adk.models.Gemini",
        "google-adk",
    ),
    (
        "strands",
        "donkey_kit.integrations.strands",
        "model",
        "strands.models.openai.OpenAIModel",
        "strands-agents",
    ),
    (
        "agent_framework",
        "donkey_kit.integrations.agent_framework",
        "chat_client",
        "agent_framework.openai.OpenAIChatCompletionClient",
        "agent-framework",
    ),
    # The same factory with api="responses"; the default is Chat Completions (#1043).
    (
        "agent_framework.responses",
        "donkey_kit.integrations.agent_framework",
        "chat_client",
        "agent_framework.openai.OpenAIChatClient",
        "agent-framework",
    ),
    (
        "openai_agents",
        "donkey_kit.integrations.openai_agents",
        "model",
        "agents.OpenAIChatCompletionsModel",
        "openai-agents",
    ),
    (
        "anthropic",
        "donkey_kit.integrations.anthropic",
        "client",
        "anthropic.AsyncAnthropic",
        "anthropic",
    ),
    # crewai.LLM is a factory: openai/ + base_url returns this native provider.
    # Pin the concrete class — every other provider is a crewai.BaseLLM too.
    (
        "crewai",
        "donkey_kit.integrations.crewai",
        "llm",
        "crewai.llms.providers.openai.completion.OpenAICompletion",
        "crewai",
    ),
    (
        "llamaindex",
        "donkey_kit.integrations.llamaindex",
        "llm",
        "llama_index.llms.openai_like.OpenAILike",
        "llama-index-llms-openai-like",
    ),
]

PROXY_ENV = (
    "DONKEY_LLM_PROXY_URL",
    "DONKEY_LLM_PROXY_CLIENT_ID",
    "DONKEY_LLM_PROXY_CLIENT_SECRET",
)
MODEL = os.environ.get("DEMO_MODEL", "gpt-4o")
# adk.gemini needs a Format=Gemini proxy; the default DDK proxies are Format=OpenAI.
# Its live round-trip runs only when this points at one (same consumer creds).
GEMINI_PROXY_ENV = "DONKEY_GEMINI_PROXY_URL"
GEMINI_MODEL = os.environ.get("DEMO_GEMINI_MODEL", "gemini-2.5-flash")
# The Anthropic client speaks the native Messages API, which needs a
# Format=Anthropic proxy; its live turn runs only when this points at one.
ANTHROPIC_PROXY_ENV = "DONKEY_ANTHROPIC_PROXY_URL"
ANTHROPIC_MODEL = os.environ.get("DEMO_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

# Check B's one user turn. Generic on purpose: a proxy with a PII policy rejects
# anything that reads as a person, email or phone number.
LIVE_PROMPT = "Reply with the single word: hello."
LIVE_AGENT_NAME = "greeter"
LIVE_INSTRUCTIONS = "You are a concise assistant. Answer in as few words as possible."


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
            if self.live == "ok":
                return "VERIFIED (LIVE)"
            # An offline-only pass (no --live, or --live skipped) confirms
            # construction + isinstance against the recorded class path — not
            # a real-sandbox round-trip, so it must not read "VERIFIED" on its
            # own (§0.3; docs/verified-apis.md §8 reserves that word for a
            # live gateway request, #681).
            if self.live.startswith("fail"):
                return "UNVERIFIED (signature confirmed; live check FAILED)"
            return "UNVERIFIED (signature confirmed)"
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


def _distribution_installed(distribution: str) -> bool:
    from importlib.metadata import PackageNotFoundError, version

    try:
        version(distribution)
    except PackageNotFoundError:
        return False
    return True


def check_signature(
    res: Result, import_path: str, factory: str, distribution: str
) -> object | None:
    """Check A — construct the native object via the adapter factory."""
    import importlib

    try:
        mod = importlib.import_module(import_path)
    except ImportError:
        res.installed = False
        return None
    # Decided by the distribution, never by an ImportError from the factory: a
    # renamed class raises one, and crewai.LLM re-raises native-provider
    # construction errors as one. Both must fail below, not read as absent.
    if not _distribution_installed(distribution):
        res.installed = False
        return None

    fn = getattr(mod, factory)
    try:
        # Anthropic's native surface is a client; the model id is a per-call
        # argument, so its factory takes no positional model (BG §1.8 divergence).
        obj: object
        if res.framework == "anthropic":
            anthropic_url = os.environ.get(ANTHROPIC_PROXY_ENV)
            obj = fn(base_url=anthropic_url) if anthropic_url else fn()
        elif res.framework == "adk.gemini":
            obj = fn(GEMINI_MODEL, base_url=os.environ.get(GEMINI_PROXY_ENV) or None)
        elif res.framework == "agent_framework.responses":
            obj = fn(MODEL, api="responses")
        else:
            obj = fn(MODEL)
    except NotImplementedError as exc:  # blocked on verification
        res.installed = True
        res.blocked = True
        res.detail = str(exc).splitlines()[0]
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


async def _langgraph_turn(obj: Any) -> str:  # noqa: ANN401
    reply = await obj.ainvoke([("user", LIVE_PROMPT)])
    return str(getattr(reply, "content", reply))


async def _adk_turn(obj: Any) -> str:  # noqa: ANN401
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    runner = InMemoryRunner(
        agent=Agent(name=LIVE_AGENT_NAME, model=obj, instruction=LIVE_INSTRUCTIONS),
        app_name="verify-frameworks",
    )
    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id="verify"
    )
    text = ""
    async for event in runner.run_async(
        user_id="verify",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=LIVE_PROMPT)]),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            text += "".join(part.text or "" for part in event.content.parts)
    return text


async def _strands_turn(obj: Any) -> str:  # noqa: ANN401
    from strands import Agent

    # The default callback handler prints the reply to stdout.
    agent = Agent(model=obj, system_prompt=LIVE_INSTRUCTIONS, callback_handler=None)
    return str(await agent.invoke_async(LIVE_PROMPT))


async def _agent_framework_turn(obj: Any) -> str:  # noqa: ANN401
    from agent_framework import Agent

    agent = Agent(client=obj, name=LIVE_AGENT_NAME, instructions=LIVE_INSTRUCTIONS)
    return str((await agent.run(LIVE_PROMPT)).text)


async def _openai_agents_turn(obj: Any) -> str:  # noqa: ANN401
    from agents import Agent, Runner, set_tracing_disabled

    # The Agents SDK exports traces to OpenAI by default, with a key this run does not have.
    set_tracing_disabled(True)
    agent = Agent(name=LIVE_AGENT_NAME, instructions=LIVE_INSTRUCTIONS, model=obj)
    return str((await Runner.run(agent, LIVE_PROMPT)).final_output)


async def _anthropic_turn(obj: Any) -> str:  # noqa: ANN401
    message = await obj.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": LIVE_PROMPT}],
    )
    return "".join(block.text for block in message.content if block.type == "text")


async def _crewai_turn(obj: Any) -> str:  # noqa: ANN401
    from crewai import Agent, Crew, Task

    agent = Agent(
        role="Assistant",
        goal="Answer the request briefly.",
        backstory=LIVE_INSTRUCTIONS,
        llm=obj,
        allow_delegation=False,
        verbose=False,
    )
    task = Task(description=LIVE_PROMPT, expected_output="One word.", agent=agent)
    crew = Crew(agents=[agent], tasks=[task], verbose=False)
    # kickoff() is sync; a thread keeps it off this event loop.
    return str((await asyncio.to_thread(crew.kickoff)).raw)


async def _llamaindex_turn(obj: Any) -> str:  # noqa: ANN401
    from llama_index.core.agent.workflow import FunctionAgent

    agent = FunctionAgent(tools=[], llm=obj, system_prompt=LIVE_INSTRUCTIONS)
    return str(await agent.run(user_msg=LIVE_PROMPT))


# Check B per framework: the framework's smallest agent (or, for Anthropic, its
# client call) around the object the adapter returned, one user turn. Each call
# was first run live by the acceptance suite's group 7 on 2026-10-13 (#34).
LIVE_TURNS: dict[str, Callable[[Any], Awaitable[str]]] = {
    "langgraph": _langgraph_turn,
    "adk": _adk_turn,
    "strands": _strands_turn,
    "agent_framework": _agent_framework_turn,
    "agent_framework.responses": _agent_framework_turn,
    "openai_agents": _openai_agents_turn,
    "anthropic": _anthropic_turn,
    "crewai": _crewai_turn,
    "llamaindex": _llamaindex_turn,
}


async def check_live(res: Result, obj: object) -> None:
    """Check B — one real turn through the framework's own native call.

    Each framework runs its smallest agent around the adapter's object
    (``LIVE_TURNS``); a reply that comes back empty fails the check. ADK's native
    Gemini model and the Anthropic client need a proxy in their own wire format,
    so they run only when ``DONKEY_GEMINI_PROXY_URL`` / ``DONKEY_ANTHROPIC_PROXY_URL``
    point at one.
    """
    if res.framework == "anthropic" and not os.environ.get(ANTHROPIC_PROXY_ENV):
        res.live = f"skipped: set {ANTHROPIC_PROXY_ENV} to a Format=Anthropic proxy"
        return
    turn = LIVE_TURNS.get(res.framework)
    if turn is not None:
        try:
            text = await turn(obj)
        except Exception as exc:  # noqa: BLE001
            res.live = f"fail: {type(exc).__name__}: {exc}"
            return
        if text.strip():
            res.live = "ok"
            res.detail = f"completion: {text!r}"
        else:
            res.live = "fail: the framework returned an empty reply"
        return
    if res.framework == "adk.gemini":
        # ADK's model-level call (BaseLlm.generate_content_async), live-verified
        # against ddk-gemini-inbound on 2026-09-29 (#691).
        if not os.environ.get(GEMINI_PROXY_ENV):
            res.live = f"skipped: set {GEMINI_PROXY_ENV} to a Format=Gemini proxy"
            return
        try:
            from google.adk.models.llm_request import LlmRequest
            from google.genai import types

            request = LlmRequest(
                model=GEMINI_MODEL,
                contents=[types.Content(role="user", parts=[types.Part(text="Say hi.")])],
            )
            text = ""
            async for resp in obj.generate_content_async(request):  # type: ignore[attr-defined]
                if resp.content and resp.content.parts:
                    text += "".join(p.text or "" for p in resp.content.parts)
            res.live = "ok"
            res.detail = f"completion: {text!r}"
        except Exception as exc:  # noqa: BLE001
            res.live = f"fail: {type(exc).__name__}: {exc}"
        return
    res.live = f"skipped: no live turn defined for {res.framework!r}"


async def run(only: list[str] | None, live: bool) -> list[Result]:
    have_real = _ensure_proxy_env_for_offline()
    results: list[Result] = []
    for key, import_path, factory, expected, distribution in FRAMEWORKS:
        if only and key not in only and key.partition(".")[0] not in only:
            continue
        res = Result(framework=key, expected_class=expected)
        obj = check_signature(res, import_path, factory, distribution)
        if live and obj is not None and res.class_matches:
            if not have_real:
                res.live = "skipped: set the 3 DONKEY_LLM_PROXY_* env vars for a live round-trip"
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
            # Match the docs/verified-apis.md §8 vocabulary exactly (#681): only
            # a real --live round-trip earns "VERIFIED"; an offline-only pass is
            # "UNVERIFIED (signature confirmed)" until a maintainer signs off.
            status = (
                "VERIFIED (LIVE)"
                if r.live == "ok"
                else "UNVERIFIED (signature confirmed; pending maintainer --live + sign-off)"
            )
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
        "--require-installed",
        action="store_true",
        help=(
            "fail if a framework named by --only is NOT INSTALLED, instead of "
            "exiting 0 (a CI leg that installed a specific extra wants this; see "
            "nightly-matrix.yml's framework-legs job, #748)"
        ),
    )
    ap.add_argument(
        "--emit-verified",
        action="store_true",
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
        r
        for r in results
        if r.installed and not r.blocked and not (r.signature_ok and r.class_matches)
    ]
    if failed:
        print(
            f"\n{len(failed)} installed framework(s) failed signature verification: "
            f"{', '.join(r.framework for r in failed)}",
            file=sys.stderr,
        )
        return 1

    # #748: a CI leg naming a framework via --only expects it to actually be
    # installed. Without this, a broken/misnamed extra install reads as NOT
    # INSTALLED above (not a signature failure) and the leg exits 0 silently —
    # the exact gap a reviewer flagged for the nightly per-framework legs.
    if args.require_installed and args.only:
        not_installed = [r for r in results if not r.installed]
        if not_installed:
            print(
                f"\n{len(not_installed)} --only framework(s) are NOT INSTALLED "
                f"(--require-installed): {', '.join(r.framework for r in not_installed)}",
                file=sys.stderr,
            )
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

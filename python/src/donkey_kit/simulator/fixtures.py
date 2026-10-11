"""The single shared fixture loader for the local gateway simulator (BG §1.4).

The simulator replays the **same files** that ``core.errors.classify()`` is
tested against, so any drift in a captured rejection shape fails the classify()
contract test AND the simulator's replay at the same time — BG §1.4's "same
files, both fail together" rule, true by construction rather than by convention.
To make that literally one read path, ``tests/unit/test_rejection_contract.py``
and ``tests/unit/test_llm_proxy_contract.py`` import :func:`parse_headers` from
here instead of keeping their own copies.

Every fixture (:func:`fixture_bytes`) is package data under
``donkey_kit/simulator/_fixtures/…`` (#746). That one directory is what a built
wheel ships, what a ``pip install``-ed ``donkey mock`` replays, and what the
classify() contract tests read in a source checkout, so the simulator and the
tests always read byte-identical files and nothing depends on the repo's
``tests/`` tree. The fixture-integrity lock lives beside them.

Nothing here imports a web framework: this module is safe under the base-only
CI job (the ``dev`` group only, no ``[local]`` extra). It imports only the framework-free
``core`` layer for the verified header names (an allowed upward import).
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json
from dataclasses import dataclass
from pathlib import Path

# Verified header names, imported from the framework-free core so the simulator
# and the client parse the identical strings (upward import, allowed under the layered
# architecture).
# RATELIMIT_HEADER is the prose budget header the live 200/403 carries (#352/#353).
from ..core import _wire
from ..core.budget import (
    LIMIT_HEADER,
    RATELIMIT_HEADER,
    REMAINING_HEADER,
    REQUEST_LIMIT_HEADER,
    REQUEST_REMAINING_HEADER,
    REQUEST_RESET_HEADER,
    RESET_HEADER,
)

__all__ = [
    "LIMIT_HEADER",
    "LOCK_PATH",
    "RATELIMIT_HEADER",
    "REMAINING_HEADER",
    "REQUEST_LIMIT_HEADER",
    "REQUEST_REMAINING_HEADER",
    "REQUEST_RESET_HEADER",
    "RESET_HEADER",
    "SHAPES",
    "TEST_LOCK_PATH",
    "Fixture",
    "compute_manifest",
    "compute_test_manifest",
    "fixture_bytes",
    "load",
    "parse_headers",
    "parse_status",
    "read_lock",
    "read_test_lock",
    "render_ratelimit_prose",
    "replay_headers",
    "write_lock",
    "write_test_lock",
]


def render_ratelimit_prose(remaining: int, limit: int, reset_ms: int) -> str:
    """Render the live ``x-llm-proxy-ratelimit`` prose header the happy-path ``200``
    carries when the ``llm-token-rate-limit`` policy is applied (#352/#353).

    The exact sentence is fixed by the live/fixture capture; defining it once here
    (shared by the simulator app's default budget window and the ``budget``
    scenario, #188) is what stops the two renderers from drifting apart.
    """
    return (
        f"Token rate limit: {remaining} tokens remaining of {limit} limit. Reset in {reset_ms}ms."
    )


# Header replay is an allow-list, not a deny-list: replay only the semantic and
# discriminator headers a client (and classify()) actually consume, and let the
# consumer generate framing (content-length, transfer-encoding, connection).
# Transport/CDN framing headers (server, cf-ray, date, strict-transport-security,
# …) are dropped — replaying them would undercut the x-donkey-simulator honesty
# guarantee and, for in-process injection, misrepresent a fixture as a live
# gateway response. content-type is NOT in the allow-list: the ASGI simulator
# carries it via the response media_type, and in-process injection re-adds it
# from Fixture.content_type — so it is never double-set. `x-request-id` and
# `x-envoy-decorator-operation` ARE kept: both are semantic gateway-identity
# headers the SDK consumes — classify() surfaces `x-request-id` as
# DonkeyError.request_id, and `donkey.last_call` parses the decorator op into the
# API-instance/environment ids (#362) — so replaying them is what makes
# simulate()/`donkey mock` populate the record from the committed fixtures. The
# honesty marker stays the injected `x-donkey-simulator: true`, not the absence
# of identity headers. The unsuffixed request-window trio is kept (#974); the
# upstream's suffixed ``x-ratelimit-*-requests`` / ``-tokens`` passthrough is not.
_KEEP_EXACT = frozenset(
    {
        _wire.REQUEST_LIMIT_HEADER,
        _wire.REQUEST_REMAINING_HEADER,
        _wire.REQUEST_RESET_HEADER,
        _wire.WWW_AUTHENTICATE_HEADER,
        _wire.INJECTION_PROTECTION_HEADER,
        _wire.CORRELATION_ID_HEADER.lower(),
        _wire.REQUEST_ID_HEADER,
        _wire.DECORATOR_OPERATION_HEADER,
    }
)
_KEEP_PREFIX = (_wire.TOKEN_HEADER_PREFIX, _wire.LLM_PROXY_HEADER_PREFIX)


def parse_headers(text: str) -> dict[str, str]:
    """Parse a captured ``*.headers.txt`` block into a lowercased header dict.

    Identical rule to the retired ``_headers`` helpers in the contract tests
    (which now import this): skip the ``HTTP/…`` status line and any line
    without a colon; split on the first colon; lowercase the key; strip both
    sides. Keeping this the single implementation is what makes "same files,
    same parser" hold for both the tests and the simulator.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("HTTP/") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip().lower()] = v.strip()
    return out


def parse_status(text: str) -> int | None:
    """Parse the numeric status from a captured ``HTTP/1.1 <code> …`` first line,
    or ``None`` if the block has no recognisable status line."""
    for line in text.splitlines():
        if line.startswith("HTTP/"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
            return None
    return None


@dataclass(frozen=True)
class _Spec:
    """Where a shape's bytes live and how to fall back when a capture is partial.

    ``headers``/``body`` are filenames within ``directory`` (or ``None`` when the
    real capture has no such file — e.g. an empty ``.body.empty`` body, or the
    model-not-found 400 which was captured body-only with no header block).
    ``fallback_status``/``fallback_content_type`` are used only when there is no
    ``headers`` file to read the status/content-type from.
    """

    directory: str  # "rejections" or "anypoint/llm_proxy"
    headers: str | None
    body: str | None
    fallback_status: int
    fallback_content_type: str | None = None


# The full shape table. Keys are the canonical shape names the simulator and the
# tests share. The policy-rejection rows classify() is tested against, plus
# the consumer-auth 401, the gateway's bare-model-name 400, the happy path, the
# SSE stream, the /models 404, and the /chat/completions happy path and stream.
SHAPES: dict[str, _Spec] = {
    # --- the documented rejection shapes (#181, +#289 regex-prompt-guard /
    #     content-safety) ---
    "token-rate-limit": _Spec(
        "anypoint/llm_proxy", "reject.token-rate-limit.headers.txt", None, 429
    ),
    # The stock rate-limiting policy's request-count 429 (docs/verified-apis.md
    # §4, #974): a flat-string body and the x-ratelimit-* trio.
    "request-rate-limit": _Spec(
        "anypoint/request_rate_limit",
        "reject.request-rate-limit.headers.txt",
        "reject.request-rate-limit.body.json",
        429,
    ),
    "pii-detected": _Spec(
        "anypoint/llm_proxy",
        "reject.pii-detected.headers.txt",
        "reject.pii-detected.body.json",
        403,
    ),
    "injection-protection": _Spec(
        "rejections",
        "reject.injection-protection.headers.txt",
        "reject.injection-protection.body.json",
        400,
    ),
    "regex-prompt-guard": _Spec(
        "rejections",
        "reject.regex-prompt-guard.headers.txt",
        "reject.regex-prompt-guard.body.json",
        403,
    ),
    "content-safety": _Spec(
        "rejections",
        "reject.content-safety.headers.txt",
        "reject.content-safety.body.json",
        403,
    ),
    "content-moderation": _Spec("rejections", "reject.content-moderation.headers.txt", None, 400),
    # Agent Kill Switch (docs/verified-apis.md §4, #694): nested error.code == "agent_killed".
    "agent-killed": _Spec(
        "rejections",
        "reject.agent-killed.headers.txt",
        "reject.agent-killed.body.json",
        403,
    ),
    "model-not-found": _Spec(
        # Captured body-only (no .headers.txt): matches test_row5's
        # httpx.Response(400, json=body) with no headers.
        "anypoint/llm_proxy",
        None,
        "reject.model-not-found.body.json",
        400,
        "application/json",
    ),
    "upstream-5xx": _Spec("rejections", "reject.upstream-5xx.headers.txt", None, 503),
    # --- the gateway's own bare-model-name 400 (NOT one of the eight;
    #     classify() → ModelNotRoutable, docs/verified-apis.md §4, #825/#891) ---
    "model-not-routable": _Spec(
        "anypoint/llm_proxy",
        "reject.model-not-routable.headers.txt",
        "reject.model-not-routable.body.json",
        400,
    ),
    # --- consumer-auth 401 (NOT one of the eight; classify() → AuthError) ---
    "client-id-missing": _Spec(
        "anypoint/llm_proxy",
        "reject.client-id-missing.headers.txt",
        "reject.client-id-missing.body.json",
        401,
    ),
    # --- happy path, stream, and the /models 404 ---
    "success": _Spec(
        "anypoint/llm_proxy",
        "responses.success.headers.txt",
        "responses.success.body.json",
        200,
    ),
    # A SEMANTIC-routing 200 (docs/verified-apis.md §3 semantic-routing row,
    # #589/#590). The 'Finance' topic capture (openai/gpt-5-mini, score 0.62):
    # routing_type Semantic plus the semantic-only
    # ``x-llm-proxy-semantic-routing-success`` prose that ``LastCall`` parses for
    # ``matched_topic`` + ``routing_score`` (#601). Its own capture directory,
    # force-included into the wheel alongside the other served dirs.
    "success-semantic": _Spec(
        "anypoint/semantic_routing",
        "responses.finance.headers.txt",
        "responses.finance.body.json",
        200,
    ),
    "stream": _Spec(
        "anypoint/llm_proxy",
        "responses.stream.headers.txt",
        "responses.stream.sample.sse",
        200,
    ),
    "models-notfound": _Spec("anypoint/llm_proxy", "models.notfound.headers.txt", None, 404),
    # --- the /chat/completions happy path (#895) ---
    # The non-streaming 200 is a live capture (docs/verified-apis.md §2, Chat
    # Completions row): the Azure OpenAI model-based route, #896.
    "chat-success": _Spec(
        "anypoint/azure_openai_routing",
        "responses.chat-completions.success.headers.txt",
        "responses.chat-completions.success.body.json",
        200,
    ),
    # The stream is NOT a capture: no streamed OpenAI-route /chat/completions
    # response has been kept (§2), so it is OpenAI's public chunk shape, in its
    # own directory with a README that says so, until #894 captures it.
    "chat-stream": _Spec(
        "openai_public",
        "chat-completions.stream.headers.txt",
        "chat-completions.stream.sse",
        200,
    ),
}

# The package-data directory every row resolves to. Rows 3/4/6 live in
# _fixtures/rejections/; the rest alias _fixtures/anypoint/llm_proxy/.
_PACKAGED_ROOT = importlib.resources.files("donkey_kit.simulator") / "_fixtures"


def fixture_bytes(directory: str, name: str) -> bytes:
    """Return the raw bytes of one packaged fixture file. Raises a clear,
    actionable error if it is missing rather than serving a fabricated body
    (verification discipline)."""
    res = _PACKAGED_ROOT
    # Chain single-segment ``/`` joins for 3.10 compatibility (multi-arg
    # joinpath() only landed in 3.11).
    for part in (directory, name):
        res = res / part
    if not res.is_file():
        raise FileNotFoundError(
            f"Simulator fixture {directory}/{name!r} is not packaged under "
            "donkey_kit/simulator/_fixtures/. The installed donkey-kit is "
            "incomplete; reinstall it."
        )
    return res.read_bytes()


@dataclass(frozen=True)
class Fixture:
    """A fully resolved captured shape: what the simulator serves and what the
    tests assert against. ``body`` is ``b""`` for the empty-body captures."""

    shape: str
    status: int
    headers: dict[str, str]
    body: bytes
    content_type: str | None


def load(shape: str) -> Fixture:
    """Resolve one shape from the table into status + headers + body bytes.

    Status and content-type come from the captured ``*.headers.txt`` when there
    is one; otherwise the spec's fallbacks are used (the model-not-found 400 has
    no header block). The headers dict is the *raw* capture — the app decides
    which headers to replay vs. strip; see :mod:`donkey_kit.simulator.app`.
    """
    try:
        spec = SHAPES[shape]
    except KeyError:
        raise KeyError(f"unknown simulator fixture shape: {shape!r}") from None

    headers: dict[str, str] = {}
    status = spec.fallback_status
    if spec.headers is not None:
        header_text = fixture_bytes(spec.directory, spec.headers).decode("utf-8")
        headers = parse_headers(header_text)
        parsed = parse_status(header_text)
        if parsed is not None:
            status = parsed

    body = fixture_bytes(spec.directory, spec.body) if spec.body is not None else b""
    content_type = headers.get("content-type", spec.fallback_content_type)
    return Fixture(
        shape=shape, status=status, headers=headers, body=body, content_type=content_type
    )


def replay_headers(fixture: Fixture) -> dict[str, str]:
    """The semantic/discriminator subset of a fixture's captured headers to replay
    verbatim, per the :data:`_KEEP_EXACT` / :data:`_KEEP_PREFIX` allow-list.

    content-type is excluded (the ASGI app rides it on the response media_type;
    in-process injection re-adds it from ``Fixture.content_type``), so a caller
    that needs it must set it explicitly rather than expect it here. Shared by the
    simulator app and ``simulate()`` (#187/#190) so both replay one identical
    subset."""
    out: dict[str, str] = {}
    for key, value in fixture.headers.items():
        if key == "content-type":
            continue
        if key in _KEEP_EXACT or key.startswith(_KEEP_PREFIX):
            out[key] = value
    return out


# --- fixture integrity lock (BG §1.4 honesty, #189) --------------------------
#
# The "same files, both fail together" rule (above) catches a fixture edit that
# changes a discriminator classify() asserts on. It does NOT catch a *benign*
# byte edit — reformatted JSON, an added field, a whitespace tweak — that leaves
# every assertion green while silently drifting the bytes the simulator replays
# away from the captured shape. That is exactly the "subtly wrong simulator"
# BG §1.4 warns is worse than none.
#
# The lock closes that gap: a committed sha256 of every fixture file the
# simulator serves, checked in tests/unit/test_fixture_integrity.py. Any byte
# change fails loudly until the lock is regenerated with
# ``python -m donkey_kit.simulator.fixtures --relock`` — the deliberate,
# reviewable "I re-captured this, I meant it" step. This is an integrity
# assertion, not fixture-capture tooling (which #189 puts out of scope).
#
# ``LOCK_PATH`` is public and writable, so it must remain a concrete ``Path``.
# Normal wheel installs and editable checkouts are both on disk. Zip-imported
# packages can still read fixture bytes through Traversable, but cannot expose
# or rewrite the lock through this Path-based public API.
LOCK_PATH = Path(__file__).resolve().parent / "_fixtures" / "fixtures.lock"
# A source checkout is the one layout where a relock produces a file to commit:
# fixtures.py sits at python/src/donkey_kit/simulator/, so parents[3] is python/.
_CHECKOUT_PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"
# tests/fixtures/ lives one level below python/ (parents[3] above), alongside
# pyproject.toml. Only meaningful in a source checkout — see TEST_LOCK_PATH below.
_CHECKOUT_TESTS_FIXTURES = _CHECKOUT_PYPROJECT.parent / "tests" / "fixtures"


def _served_files() -> list[tuple[str, str]]:
    """The (directory, name) of every fixture file the simulator serves, deduped
    and sorted. Derived from :data:`SHAPES` so a new shape is locked automatically
    the moment it is added to the table."""
    seen: set[tuple[str, str]] = set()
    for spec in SHAPES.values():
        for name in (spec.headers, spec.body):
            if name is not None:
                seen.add((spec.directory, name))
    return sorted(seen)


def compute_manifest() -> dict[str, str]:
    """A ``{"<directory>/<name>": "sha256:<hex>"}`` map over every fixture file
    the simulator serves, computed from the bytes on disk right now."""
    return {
        f"{directory}/{name}": "sha256:"
        + hashlib.sha256(fixture_bytes(directory, name)).hexdigest()
        for directory, name in _served_files()
    }


def read_lock() -> dict[str, str]:
    """The committed manifest at :data:`LOCK_PATH`."""
    data: dict[str, str] = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return data


def write_lock() -> None:
    """Regenerate :data:`LOCK_PATH` from the current fixture bytes. Called by
    ``python -m donkey_kit.simulator.fixtures --relock`` after a re-capture.

    From an installed wheel this updates that environment's packaged lock; the
    ``--relock`` command rejects that layout because it cannot produce a file
    to commit.
    """
    LOCK_PATH.write_text(
        json.dumps(compute_manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# --- test-only fixture integrity lock (#752) ----------------------------------
#
# The lock above closes the honesty gap only for the files the simulator serves
# (``SHAPES``, packaged under src/donkey_kit/simulator/_fixtures/). The
# much larger ``tests/fixtures/`` tree (model_wallet/, gemini_inbound/,
# semantic_cache/, anthropic_inbound/, a2d/, openai_gemini_stream/, …) is test-only
# captures: real traffic the error-classification and transport tests are pinned
# to, but never read by the simulator and never packaged (#746's "one directory
# a wheel ships" rule is about ``_fixtures/``, not ``tests/``). Those files had no
# lock at all, so a hand-edit there went undetected — the exact gap TEST-07
# (code review, commit 11b806b) flagged.
#
# This is a second, independent lock over a disjoint file set, deliberately kept
# as its own manifest rather than folded into ``compute_manifest()``/``LOCK_PATH``:
# the two lock files have different shipping rules (``LOCK_PATH`` is package data
# a wheel ships; ``TEST_LOCK_PATH`` is a tests/-tree file a wheel must NEVER ship,
# since #746 forbids force-including anything from tests/ into the build — see
# test_wheel_build_takes_nothing_from_the_test_tree). Mixing them into one
# manifest would make that boundary one `if` away from being crossed by accident.
# Same discipline otherwise: committed sha256 per file, checked in
# tests/unit/test_fixture_integrity.py, regenerated only by
# ``python -m donkey_kit.simulator.fixtures --relock`` (same command, same
# "I re-captured this, I meant it" step — it rewrites both locks that exist in
# the checkout it is run from).
TEST_LOCK_PATH = _CHECKOUT_TESTS_FIXTURES / "fixtures.lock"
# Local litter git already ignores (.gitignore) and no test reads. Locking it
# would make a macOS relock commit a ``.DS_Store`` row that every clean CI
# checkout then reports as a removed fixture.
_UNLOCKED_NAMES = frozenset({".DS_Store", "__pycache__"})


def _test_fixture_files() -> list[Path]:
    """Every file under ``tests/fixtures/`` (recursively), sorted, excluding the
    lock itself and git-ignored OS/bytecode litter. Dev-only: raises if not run
    from a source checkout — a wheel install has no ``tests/`` tree to walk."""
    if not _CHECKOUT_TESTS_FIXTURES.is_dir():
        raise FileNotFoundError(
            f"{_CHECKOUT_TESTS_FIXTURES} does not exist; the test-fixture lock "
            "only applies to a source checkout, not an installed wheel"
        )
    return sorted(
        path
        for path in _CHECKOUT_TESTS_FIXTURES.rglob("*")
        if path.is_file()
        and path != TEST_LOCK_PATH
        and _UNLOCKED_NAMES.isdisjoint(path.relative_to(_CHECKOUT_TESTS_FIXTURES).parts)
    )


def compute_test_manifest() -> dict[str, str]:
    """A ``{"<relative/path>": "sha256:<hex>"}`` map over every file under
    ``tests/fixtures/``, computed from the bytes on disk right now. Keys use
    POSIX-style separators so the manifest is stable across platforms."""
    return {
        path.relative_to(_CHECKOUT_TESTS_FIXTURES).as_posix(): "sha256:"
        + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in _test_fixture_files()
    }


def read_test_lock() -> dict[str, str]:
    """The committed manifest at :data:`TEST_LOCK_PATH`."""
    data: dict[str, str] = json.loads(TEST_LOCK_PATH.read_text(encoding="utf-8"))
    return data


def write_test_lock() -> None:
    """Regenerate :data:`TEST_LOCK_PATH` from the current ``tests/fixtures/``
    bytes. Called by ``python -m donkey_kit.simulator.fixtures --relock``
    alongside :func:`write_lock`, after a re-capture under ``tests/fixtures/``."""
    TEST_LOCK_PATH.write_text(
        json.dumps(compute_test_manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _main() -> None:
    """Run the dev-only relock command."""
    import argparse

    parser = argparse.ArgumentParser(description="Simulator fixture integrity lock.")
    parser.add_argument(
        "--relock",
        action="store_true",
        help="regenerate fixtures.lock and tests/fixtures/fixtures.lock from the current bytes",
    )
    args = parser.parse_args()
    if args.relock:
        if not _CHECKOUT_PYPROJECT.is_file():
            parser.error(
                "--relock must run from an editable source checkout so it updates "
                "src/donkey_kit/simulator/_fixtures/fixtures.lock"
            )
        write_lock()
        print(f"wrote {LOCK_PATH} ({len(compute_manifest())} fixtures)")
        if _CHECKOUT_TESTS_FIXTURES.is_dir():
            write_test_lock()
            print(f"wrote {TEST_LOCK_PATH} ({len(compute_test_manifest())} fixtures)")
    else:
        parser.error("nothing to do; pass --relock to regenerate the lock")


if __name__ == "__main__":  # pragma: no cover - dev-only relock entrypoint
    _main()

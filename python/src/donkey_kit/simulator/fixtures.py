"""The single shared fixture loader for the local gateway simulator (BG §1.4).

The simulator replays the **same files** that ``core.errors.classify()`` is
tested against, so any drift in a captured rejection shape fails the classify()
contract test AND the simulator's replay at the same time — BG §1.4's "same
files, both fail together" rule, true by construction rather than by convention.
To make that literally one read path, ``tests/unit/test_rejection_contract.py``
and ``tests/unit/test_llm_proxy_contract.py`` import :func:`parse_headers` from
here instead of keeping their own copies.

Resolution order for every fixture (:func:`fixture_bytes`):

1. Packaged ``donkey_kit/simulator/_fixtures/…`` — present in a built wheel via
   the ``[tool.hatch.build.targets.wheel.force-include]`` mapping in
   ``pyproject.toml`` (so a ``pip install``-ed ``donkey mock`` finds them).
2. Source-checkout fallback ``python/tests/fixtures/…`` — where the classify()
   tests read from, so in a source tree the simulator and the tests read the
   byte-identical files.

The fixture-integrity lock follows the same resolution order, so the public
lock helpers work from both a source checkout and an installed wheel.

Nothing here imports a web framework: this module is safe under the base-only
CI job (``[dev]`` only, no ``[local]`` extra). It imports only the framework-free
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
from ..core.budget import (
    LIMIT_HEADER,
    RATELIMIT_HEADER,
    REMAINING_HEADER,
    RESET_HEADER,
)

__all__ = [
    "LIMIT_HEADER",
    "LOCK_PATH",
    "RATELIMIT_HEADER",
    "REMAINING_HEADER",
    "RESET_HEADER",
    "Fixture",
    "SHAPES",
    "compute_manifest",
    "fixture_bytes",
    "load",
    "parse_headers",
    "parse_status",
    "read_lock",
    "render_ratelimit_prose",
    "replay_headers",
    "write_lock",
]


def render_ratelimit_prose(remaining: int, limit: int, reset_ms: int) -> str:
    """Render the live ``x-llm-proxy-ratelimit`` prose header the happy-path ``200``
    carries when the ``llm-token-rate-limit`` policy is applied (#352/#353).

    The exact sentence is fixed by the live/fixture capture; defining it once here
    (shared by the simulator app's default budget window and the ``budget``
    scenario, #188) is what stops the two renderers from drifting apart.
    """
    return (
        f"Token rate limit: {remaining} tokens remaining of "
        f"{limit} limit. Reset in {reset_ms}ms."
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
# of identity headers.
_KEEP_EXACT = frozenset(
    {
        "www-authenticate",
        "x-injection-protection",
        "x-correlation-id",
        "x-request-id",
        "x-envoy-decorator-operation",
    }
)
_KEEP_PREFIX = ("x-token-", "x-llm-proxy-")


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
# tests share. The eight policy-rejection rows classify() is tested against, plus
# the consumer-auth 401, the happy path, the SSE stream, and the /models 404.
SHAPES: dict[str, _Spec] = {
    # --- the documented rejection shapes (#181, +#289 regex-prompt-guard /
    #     content-safety) ---
    "token-rate-limit": _Spec(
        "anypoint/llm_proxy", "reject.token-rate-limit.headers.txt", None, 429
    ),
    "pii-detected": _Spec(
        "anypoint/llm_proxy",
        "reject.pii-detected.headers.txt",
        "reject.pii-detected.body.json",
        403,
    ),
    "injection-protection": _Spec(
        "rejections", "reject.injection-protection.headers.txt", None, 400
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
    "content-moderation": _Spec(
        "rejections", "reject.content-moderation.headers.txt", None, 400
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
    "stream": _Spec(
        "anypoint/llm_proxy",
        "responses.stream.headers.txt",
        "responses.stream.sample.sse",
        200,
    ),
    "models-notfound": _Spec(
        "anypoint/llm_proxy", "models.notfound.headers.txt", None, 404
    ),
}

# The two source directories rows resolve to in a checkout. Rows 3/4/6 live in
# tests/fixtures/rejections/; the rest alias tests/fixtures/anypoint/llm_proxy/.
# Derived from this module's location: fixtures.py is at
# python/src/donkey_kit/simulator/fixtures.py, so parents[3] is python/.
_SOURCE_ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
_PACKAGED_ROOT = importlib.resources.files("donkey_kit.simulator") / "_fixtures"


def _packaged_bytes(directory: str, name: str) -> bytes | None:
    """Read ``_fixtures/<directory>/<name>`` from the installed package, or
    ``None`` if it is not present (e.g. an editable/source checkout that never
    ran the wheel force-include)."""
    res = _PACKAGED_ROOT
    # Chain single-segment ``/`` joins for 3.10 compatibility (multi-arg
    # joinpath() only landed in 3.11).
    for part in (directory, name):
        res = res / part
    if res.is_file():
        return res.read_bytes()
    return None


def _source_bytes(directory: str, name: str) -> bytes | None:
    path = _SOURCE_ROOT / directory / name
    if path.is_file():
        return path.read_bytes()
    return None


def fixture_bytes(directory: str, name: str) -> bytes:
    """Return the raw bytes of one fixture file, packaged copy first then the
    source-checkout fallback. Raises a clear, actionable error if neither is
    found rather than serving a fabricated body (verification discipline)."""
    data = _packaged_bytes(directory, name)
    if data is not None:
        return data
    data = _source_bytes(directory, name)
    if data is not None:
        return data
    raise FileNotFoundError(
        f"Simulator fixture {directory}/{name!r} is neither packaged under "
        f"donkey_kit/simulator/_fixtures/ nor present at {_SOURCE_ROOT}. "
        "A wheel build must force-include tests/fixtures/ (see pyproject.toml)."
    )


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
_SOURCE_LOCK_PATH = _SOURCE_ROOT / "fixtures.lock"
_PACKAGED_LOCK = _PACKAGED_ROOT / "fixtures.lock"
LOCK_PATH = Path(str(_PACKAGED_LOCK)) if _PACKAGED_LOCK.is_file() else _SOURCE_LOCK_PATH


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
        f"{directory}/{name}": "sha256:" + hashlib.sha256(
            fixture_bytes(directory, name)
        ).hexdigest()
        for directory, name in _served_files()
    }


def read_lock() -> dict[str, str]:
    """The committed manifest at :data:`LOCK_PATH`, from either package layout."""
    data: dict[str, str] = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return data


def write_lock() -> None:
    """Regenerate :data:`LOCK_PATH` from the current fixture bytes. Called by
    ``python -m donkey_kit.simulator.fixtures --relock`` after a re-capture.

    In a source checkout this updates ``tests/fixtures/fixtures.lock``. From an
    installed wheel it updates that environment's packaged lock.
    """
    LOCK_PATH.write_text(
        json.dumps(compute_manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":  # pragma: no cover - dev-only relock entrypoint
    import argparse

    parser = argparse.ArgumentParser(description="Simulator fixture integrity lock.")
    parser.add_argument(
        "--relock",
        action="store_true",
        help="regenerate fixtures.lock from the current fixture bytes",
    )
    args = parser.parse_args()
    if args.relock:
        write_lock()
        print(f"wrote {LOCK_PATH} ({len(compute_manifest())} fixtures)")
    else:
        parser.error("nothing to do; pass --relock to regenerate the lock")

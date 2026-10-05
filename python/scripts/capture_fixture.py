"""One-command fixture capture: body, headers and provenance, scrubbed and
relocked (#752).

CONTRIBUTING.md's "Fixture-driven tests" section describes the capture
procedure as **capture, scrub, relock** but, before #752, had no single command
for the first step — every fixture directory's README carried its own ad hoc
instructions for how a maintainer last produced its files. This script is that
one command: it sends a single HTTP request with ``httpx``, writes the
response's status/headers and body as a captured fixture pair, appends a
provenance line to the target directory's ``README.md``, then runs the
existing scrub (:mod:`scrub_fixtures`) and regenerates both integrity locks
(:func:`donkey_kit.simulator.fixtures.write_lock` /
:func:`~donkey_kit.simulator.fixtures.write_test_lock`) so the capture is
locked before it is ever committed.

**This script must never be pointed at a live Anypoint endpoint by an
automated agent.** It is a developer tool for a maintainer capturing a *real*
sandbox response by hand (BG §1.5 — fixtures are captures, not inventions);
automation must not fabricate traffic in its name. Its own test coverage
(``tests/unit/test_capture_fixture.py``) exercises every code path offline
through ``httpx.MockTransport``.

    python scripts/capture_fixture.py \\
        --out-dir tests/fixtures/anypoint/model_wallet \\
        --name responses.success \\
        --method POST --url https://example-sandbox.anypoint.mulesoft.com/v1/chat/completions \\
        --header "client_id: $DONKEY_LLM_PROXY_CLIENT_ID" \\
        --header "client_secret: $DONKEY_LLM_PROXY_CLIENT_SECRET" \\
        --data '{"model": "openai/gpt-5-mini", "messages": [...]}' \\
        --provenance "Captured 2026-10-05 against sandbox instance 123, model-wallet policy v2."

Then review the diff, confirm nothing sensitive survived
(``python scripts/scrub_fixtures.py --check``), and commit the fixture pair,
the updated ``README.md``, and both regenerated locks together.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import httpx

from donkey_kit.simulator import fixtures

_PYTHON_ROOT = Path(__file__).resolve().parents[1]

# scrub_fixtures.py is a sibling script, not an installed module (no scripts/
# package); load it the same way tests/unit/test_scrub_fixtures.py does.
_SCRUB_SCRIPT = Path(__file__).resolve().parent / "scrub_fixtures.py"


def _load_scrub() -> object:
    spec = importlib.util.spec_from_file_location("ddk_scrub_fixtures", _SCRUB_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: Body suffix by content-type prefix, in the shapes the existing fixture
#: directories already use (``.body.json``, ``.body.txt`` for SSE/text streams).
_SUFFIX_BY_CONTENT_TYPE: tuple[tuple[str, str], ...] = (
    ("application/json", "json"),
    ("text/event-stream", "txt"),
    ("text/", "txt"),
)


def body_suffix(content_type: str | None) -> str:
    """The ``.body.<suffix>`` extension for ``content_type``, defaulting to
    ``bin`` for anything unrecognized rather than guessing a text shape."""
    if content_type is None:
        return "bin"
    lowered = content_type.lower()
    for prefix, suffix in _SUFFIX_BY_CONTENT_TYPE:
        if lowered.startswith(prefix):
            return suffix
    return "bin"


def parse_header_args(raw: list[str]) -> dict[str, str]:
    """``["Key: value", ...]`` -> ``{"Key": "value"}``, same shape as a captured
    ``*.headers.txt`` line. Raises ``ValueError`` on a malformed entry."""
    headers: dict[str, str] = {}
    for item in raw:
        if ":" not in item:
            raise ValueError(f"malformed --header {item!r}; expected 'Name: value'")
        name, _, value = item.partition(":")
        headers[name.strip()] = value.strip()
    return headers


def capture(
    client: httpx.Client, method: str, url: str, headers: dict[str, str], data: bytes | None
) -> httpx.Response:
    """Send the one request this capture records. No retry, no follow-redirects
    override: a fixture captures exactly what the gateway sent back for this
    exact request."""
    return client.request(method, url, headers=headers, content=data)


def render_headers_block(response: httpx.Response) -> str:
    """The ``*.headers.txt`` text for ``response``, in the existing fixtures'
    format: an ``HTTP/1.1 <code> <reason>`` status line, one ``Name: value`` per
    header, then a trailing blank line."""
    lines = [f"HTTP/1.1 {response.status_code} {response.reason_phrase}".rstrip()]
    lines.extend(f"{name}: {value}" for name, value in response.headers.items())
    return "\n".join(lines) + "\n\n"


def write_capture(response: httpx.Response, out_dir: Path, name: str) -> tuple[Path, Path | None]:
    """Write ``<name>.headers.txt`` and, if the response has a body,
    ``<name>.body.<suffix>`` under ``out_dir``. Returns the paths written (the
    body path is ``None`` for an empty body, matching the ``.body.empty``-free
    convention the existing fixtures use for header-only captures)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    headers_path = out_dir / f"{name}.headers.txt"
    headers_path.write_text(render_headers_block(response), encoding="utf-8")

    if not response.content:
        return headers_path, None

    suffix = body_suffix(response.headers.get("content-type"))
    body_path = out_dir / f"{name}.body.{suffix}"
    body_path.write_bytes(response.content)
    return headers_path, body_path


def append_provenance(out_dir: Path, name: str, provenance: str) -> Path:
    """Append one ``- `<name>`: <provenance>`` line to ``out_dir/README.md``,
    creating the file (with a one-line heading) if this is the directory's
    first capture."""
    readme_path = out_dir / "README.md"
    line = f"- `{name}`: {provenance}\n"
    if readme_path.is_file():
        text = readme_path.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            text += "\n"
        readme_path.write_text(text + line, encoding="utf-8")
    else:
        readme_path.write_text(f"# Fixture provenance\n\n{line}", encoding="utf-8")
    return readme_path


def relock(scrub_module: object) -> None:
    """Scrub the two fixture trees, then regenerate both integrity locks
    (#752) — the same two steps CONTRIBUTING.md's "capture, scrub, relock"
    procedure names, run as one step at the end of a capture."""
    scrubbed = scrub_module.scrub(  # type: ignore[attr-defined]
        [
            _PYTHON_ROOT / "tests" / "fixtures",
            _PYTHON_ROOT / "src" / "donkey_kit" / "simulator" / "_fixtures",
        ]
    )
    for path in scrubbed:
        print(f"scrubbed {path}")

    fixtures.write_lock()
    fixtures.write_test_lock()
    print(f"wrote {fixtures.LOCK_PATH}")
    print(f"wrote {fixtures.TEST_LOCK_PATH}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--out-dir",
        required=True,
        type=Path,
        help="fixture directory to write into, e.g. tests/fixtures/anypoint/model_wallet",
    )
    parser.add_argument("--name", required=True, help="fixture shape name, e.g. responses.success")
    parser.add_argument("--method", default="GET", help="HTTP method (default: GET)")
    parser.add_argument("--url", required=True, help="the endpoint to capture")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="'Name: value'",
        help="repeatable; one request header per flag",
    )
    parser.add_argument("--data", default=None, help="raw request body, if any")
    parser.add_argument(
        "--provenance",
        required=True,
        help="one line for the directory README: when, against what, what triggered this shape",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="request timeout in seconds (default: 30)"
    )
    args = parser.parse_args(argv)

    try:
        headers = parse_header_args(args.header)
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover - argparse.error() already exits

    data = args.data.encode("utf-8") if args.data is not None else None
    with httpx.Client(timeout=args.timeout) as client:
        response = capture(client, args.method, args.url, headers, data)

    headers_path, body_path = write_capture(response, args.out_dir, args.name)
    print(f"wrote {headers_path}")
    if body_path is not None:
        print(f"wrote {body_path}")

    readme_path = append_provenance(args.out_dir, args.name, args.provenance)
    print(f"updated {readme_path}")

    relock(_load_scrub())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

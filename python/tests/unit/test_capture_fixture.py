"""``scripts/capture_fixture.py``, the one-command fixture capture (#752).

Never exercised against a real endpoint here: every request goes through
``httpx.MockTransport``, matching the module's own rule that it must not be run
against a live endpoint except by a maintainer, by hand.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import httpx
import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "capture_fixture.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ddk_capture_fixture", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture_fixture = _load()


def _client(handler: httpx.MockTransport | None = None) -> httpx.Client:
    if handler is None:
        handler = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/json", "x-request-id": "req-123"},
                json={"ok": True},
            )
        )
    return httpx.Client(transport=handler)


def test_parse_header_args_splits_on_first_colon() -> None:
    assert capture_fixture.parse_header_args(["client_id: abc", "x-trace: a:b:c"]) == {
        "client_id": "abc",
        "x-trace": "a:b:c",
    }


def test_parse_header_args_rejects_a_malformed_entry() -> None:
    with pytest.raises(ValueError, match="malformed"):
        capture_fixture.parse_header_args(["no-colon-here"])


@pytest.mark.parametrize(
    ("content_type", "suffix"),
    [
        ("application/json", "json"),
        ("application/json; charset=utf-8", "json"),
        ("text/event-stream", "txt"),
        ("text/plain", "txt"),
        ("application/octet-stream", "bin"),
        (None, "bin"),
    ],
)
def test_body_suffix_maps_content_type(content_type: str | None, suffix: str) -> None:
    assert capture_fixture.body_suffix(content_type) == suffix


def test_capture_sends_exactly_the_requested_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    with _client(httpx.MockTransport(handler)) as client:
        response = capture_fixture.capture(
            client,
            "POST",
            "https://example.invalid/v1/chat/completions",
            {"client_id": "abc"},
            b'{"a": 1}',
        )

    assert response.status_code == 200
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].headers["client_id"] == "abc"
    assert seen[0].content == b'{"a": 1}'
    # httpx would otherwise ask for gzip and decode it behind the capture's back.
    assert seen[0].headers["accept-encoding"] == "identity"


def test_capture_keeps_an_explicit_accept_encoding() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    with _client(httpx.MockTransport(handler)) as client:
        capture_fixture.capture(
            client, "GET", "https://example.invalid", {"accept-encoding": "br"}, None
        )

    assert seen[0].headers.get_list("accept-encoding") == ["br"]


def test_write_capture_refuses_a_decoded_compressed_body(tmp_path: Path) -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "application/json", "content-encoding": "gzip"},
        content=gzip.compress(b"{}"),
        request=httpx.Request("GET", "https://example.invalid"),
    )
    assert response.content == b"{}"  # httpx decoded it; the wire bytes are gone

    with pytest.raises(ValueError, match="content-encoding 'gzip'"):
        capture_fixture.write_capture(response, tmp_path, "responses.success")
    assert not any(tmp_path.iterdir())


def test_render_headers_block_keeps_every_value_of_a_repeated_header() -> None:
    response = httpx.Response(
        200,
        headers=[("set-cookie", "a=1"), ("set-cookie", "b=2")],
        request=httpx.Request("GET", "https://example.invalid"),
    )
    lines = capture_fixture.render_headers_block(response).splitlines()
    assert lines[1:3] == ["set-cookie: a=1", "set-cookie: b=2"]


def test_out_dir_must_be_inside_a_fixture_root(tmp_path: Path) -> None:
    assert capture_fixture.is_under_fixture_root(
        capture_fixture.FIXTURE_ROOTS[0] / "anypoint" / "model_wallet"
    )
    assert not capture_fixture.is_under_fixture_root(tmp_path)


def test_main_rejects_an_out_dir_that_would_go_unlocked(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="2"):
        capture_fixture.main(
            [
                "--out-dir",
                str(tmp_path),
                "--name",
                "x",
                "--url",
                "https://example.invalid",
                "--provenance",
                "p",
            ]
        )
    assert not any(tmp_path.iterdir())


def test_render_headers_block_matches_the_captured_shape() -> None:
    response = httpx.Response(
        429,
        headers=[("X-RateLimit-Remaining", "0"), ("Retry-After", "5")],
        request=httpx.Request("GET", "https://example.invalid"),
    )
    block = capture_fixture.render_headers_block(response)
    lines = block.splitlines()
    assert lines[0] == "HTTP/1.1 429 Too Many Requests"
    assert "x-ratelimit-remaining: 0" in [line.lower() for line in lines[1:]]
    assert block.endswith("\n\n")


def test_write_capture_writes_headers_and_body(tmp_path: Path) -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        json={"ok": True},
        request=httpx.Request("GET", "https://example.invalid"),
    )

    headers_path, body_path = capture_fixture.write_capture(response, tmp_path, "responses.success")

    assert headers_path == tmp_path / "responses.success.headers.txt"
    assert headers_path.read_text(encoding="utf-8").startswith("HTTP/1.1 200")
    assert body_path == tmp_path / "responses.success.body.json"
    assert json.loads(body_path.read_bytes()) == {"ok": True}


def test_write_capture_omits_body_file_for_an_empty_body(tmp_path: Path) -> None:
    response = httpx.Response(
        429,
        headers={"retry-after": "5"},
        request=httpx.Request("GET", "https://example.invalid"),
    )

    headers_path, body_path = capture_fixture.write_capture(
        response, tmp_path, "reject.token-rate-limit"
    )

    assert headers_path.is_file()
    assert body_path is None
    assert not any(tmp_path.glob("reject.token-rate-limit.body.*"))


def test_append_provenance_creates_a_new_readme(tmp_path: Path) -> None:
    readme_path = capture_fixture.append_provenance(
        tmp_path, "responses.success", "Captured 2026-10-05."
    )
    text = readme_path.read_text(encoding="utf-8")
    assert "responses.success" in text
    assert "Captured 2026-10-05." in text


def test_append_provenance_appends_to_an_existing_readme(tmp_path: Path) -> None:
    readme_path = tmp_path / "README.md"
    readme_path.write_text(
        "# model_wallet fixtures\n\n- `existing`: already here.\n", encoding="utf-8"
    )

    capture_fixture.append_provenance(tmp_path, "responses.success", "Captured 2026-10-05.")

    text = readme_path.read_text(encoding="utf-8")
    assert "- `existing`: already here." in text
    assert "- `responses.success`: Captured 2026-10-05." in text


def test_main_runs_end_to_end_against_a_mock_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full CLI path, including the scrub + relock step, offline."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["client_id"] == "test-client"
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"id": "00000000-0000-4000-8000-aaaaaaaaaaaa", "ok": True},
        )

    real_client_cls = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **_: real_client_cls(transport=httpx.MockTransport(handler))
    )

    class _FakeFixtures:
        LOCK_PATH = tmp_path / "shipped.lock"
        TEST_LOCK_PATH = tmp_path / "test.lock"
        relocked = False

        @classmethod
        def write_lock(cls) -> None:
            cls.relocked = True

        @classmethod
        def write_test_lock(cls) -> None:
            cls.relocked = True

    monkeypatch.setattr(capture_fixture, "fixtures", _FakeFixtures)

    class _NoOpScrub:
        @staticmethod
        def scrub(paths: object) -> list[Path]:
            return []

    monkeypatch.setattr(capture_fixture, "_load_scrub", lambda: _NoOpScrub())
    monkeypatch.setattr(capture_fixture, "FIXTURE_ROOTS", (tmp_path,))

    out_dir = tmp_path / "model_wallet"
    exit_code = capture_fixture.main(
        [
            "--out-dir",
            str(out_dir),
            "--name",
            "responses.success",
            "--method",
            "POST",
            "--url",
            "https://example.invalid/v1/chat/completions",
            "--header",
            "client_id: test-client",
            "--provenance",
            "Captured 2026-10-05 against a mock transport.",
        ]
    )

    assert exit_code == 0
    assert (out_dir / "responses.success.headers.txt").is_file()
    assert (out_dir / "responses.success.body.json").is_file()
    assert "Captured 2026-10-05" in (out_dir / "README.md").read_text(encoding="utf-8")
    assert _FakeFixtures.relocked is True

"""The custom-policies docs page is executable (#210).

``website/content/custom-policies.mdx`` teaches every ``classify.register()``
form as a gateway response followed by the registration that types it. This
test parses each ``http`` fence into a response, runs the ``python`` fence after
it, and asserts the response is classified as the type that fence registered
last. It also runs every ``match=dict(...)`` row of the page's rejection table
and asserts ``register()`` refuses it, so the page cannot teach a shape that
does not work, or call a working one rejected.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from donkey_kit import PolicyViolation, classify
from donkey_kit._testing import isolated_classify

_PAGE = Path(__file__).resolve().parents[3] / "website/content/custom-policies.mdx"
_TEXT = _PAGE.read_text()
_PAIRS = re.findall(r"```http\n(.*?)```\s*```python\n(.*?)```", _TEXT, re.DOTALL)
_DEFINITIONS = [
    fence
    for fence in re.findall(r"```python\n(.*?)```", _TEXT.split("## The match")[0], re.DOTALL)
    if "class " in fence
]
_REJECTED_ROWS = re.findall(r"^\| `(match=dict\(.*?\))` \|", _TEXT, re.MULTILINE)


def _response(fence: str) -> httpx.Response:
    head, _, body = fence.partition("\n\n")
    status_line, *header_lines = head.splitlines()
    headers = dict(line.split(": ", 1) for line in header_lines)
    return httpx.Response(int(status_line.split()[1]), headers=headers, json=json.loads(body))


def _namespace() -> dict[str, Any]:
    # Donkey.classify is donkey_kit.classify (test_classifier), so a stand-in will do.
    namespace: dict[str, Any] = {"donkey": SimpleNamespace(classify=classify), "re": re}
    for fence in _DEFINITIONS:
        exec(compile(fence, str(_PAGE), "exec"), namespace)
    return namespace


def test_the_page_has_every_form() -> None:
    assert len(_DEFINITIONS) == 2
    assert len(_PAIRS) == 8
    assert len(_REJECTED_ROWS) == 15


@pytest.mark.parametrize(("http", "source"), _PAIRS, ids=[f"form{i}" for i in range(len(_PAIRS))])
def test_each_documented_response_is_typed_by_its_registration(http: str, source: str) -> None:
    namespace = _namespace()
    with isolated_classify():
        exec(compile(source, str(_PAGE), "exec"), namespace)
        expected = classify.registered()[-1].raises
        error = classify(_response(http))
    assert type(error) is expected
    assert error.policy == expected.policy
    status = error.response.status_code
    assert str(error) == f"Request refused by custom policy {error.policy} ({status})."


@pytest.mark.parametrize("row", _REJECTED_ROWS)
def test_each_documented_rejection_is_rejected(row: str) -> None:
    namespace = _namespace()
    match = eval(row.removeprefix("match="), namespace)  # the page's own text
    with isolated_classify(), pytest.raises((TypeError, ValueError)):
        classify.register(match=match, raises=namespace["SalaryLeakBlocked"])


def test_the_documented_definitions_are_registrable() -> None:
    namespace = _namespace()
    for name in ("SalaryLeakBlocked", "CustomerPIIBlocked"):
        assert issubclass(namespace[name], namespace["AcmePolicyViolation"])
        assert issubclass(namespace[name], PolicyViolation)

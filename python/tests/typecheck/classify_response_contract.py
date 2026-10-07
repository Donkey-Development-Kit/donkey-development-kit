"""Static contract: ``classify()`` accepts either HTTP stack's response (#933).

Never executed: mypy checks it (``files`` in ``pyproject.toml``). openai>=3 and
anthropic>=1 type ``APIStatusError.response`` as ``httpx2.Response``, a separate
class from ``httpx.Response``; the documented bridge ``classify(exc.response)``
must type-check on both without a cast. The negative case is a
``# type: ignore[arg-type]`` that strict mode's ``warn_unused_ignores`` turns
into an error the moment ``classify()`` stops rejecting a non-response.
"""

from __future__ import annotations

import httpx
import httpx2
import openai
from typing_extensions import assert_type

from donkey_kit.core.errors import DonkeyError, ResponseLike, classify


def check_httpx(response: httpx.Response) -> None:
    assert_type(classify(response), DonkeyError)


def check_httpx2(response: httpx2.Response) -> None:
    assert_type(classify(response), DonkeyError)


def check_openai_bridge(exc: openai.APIStatusError) -> None:
    # The pattern the docs teach, with no cast or ignore.
    assert_type(classify(exc.response), DonkeyError)


def check_carried_response(err: DonkeyError) -> None:
    # The response a DonkeyError carries is either stack's, read through the
    # same Protocol classify() accepts.
    assert_type(err.response, ResponseLike | None)
    if err.response is not None:
        assert_type(err.response.status_code, int)
        assert_type(err.response.request.headers.get("x-correlation-id"), str | None)


def check_rejects_non_response() -> None:
    classify(object())  # type: ignore[arg-type]

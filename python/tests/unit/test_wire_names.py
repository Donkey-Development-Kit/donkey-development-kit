"""Gateway header names are spelled out once, in ``core/_wire`` (#720).

A second copy of a wire name is how two definitions drift apart, so no module
under ``src/`` other than ``core/_wire.py`` may contain a gateway header name
as a string literal (docstrings and prose are fine; only an exact match counts).
"""

from __future__ import annotations

import ast
from pathlib import Path

from donkey_kit.core import _verify, _wire

_SRC = Path(_wire.__file__).resolve().parents[1]
_WIRE_FILE = Path(_wire.__file__).resolve()

# ``client_id`` / ``client_secret`` are also config keys and OAuth form fields,
# and ``authorization`` is a generic HTTP header, so a bare literal of one of
# them is not necessarily a gateway header. Every other name is distinctive.
_AMBIGUOUS = {"client_id", "client_secret", "authorization"}


def _wire_names() -> dict[str, str]:
    """Lower-cased header name (or prefix) -> its ``_wire`` constant."""
    return {
        value.lower(): name
        for name, value in vars(_wire).items()
        if name.endswith(("_HEADER", "_HEADER_PLACEHOLDER", "_PREFIX"))
        and isinstance(value, str)
        and value.lower() not in _AMBIGUOUS
    }


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _wire_literals(path: Path, names: dict[str, str]) -> list[str]:
    """Every non-docstring string literal in ``path`` that is a wire name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_nodes(tree)
    return [
        f"{node.lineno}: {node.value!r} is _wire.{names[node.value.lower()]}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and node.value.lower() in names
    ]


def test_no_gateway_header_literal_outside_the_wire_module() -> None:
    names = _wire_names()
    found = [
        f"{path.relative_to(_SRC)}:{hit}"
        for path in sorted(_SRC.rglob("*.py"))
        if path.resolve() != _WIRE_FILE
        for hit in _wire_literals(path, names)
    ]
    assert not found, "Import these from donkey_kit.core._wire instead:\n" + "\n".join(found)


def test_the_scan_catches_a_recased_literal_but_not_prose(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text(
        '"""Reads ``x-token-reset``."""\n'
        'RESET = "X-Token-Reset"\n'
        'MESSAGE = "see the x-token-reset header"\n'
    )
    assert _wire_literals(planted, _wire_names()) == [
        "2: 'X-Token-Reset' is _wire.TOKEN_RESET_HEADER"
    ]


def test_verify_binds_the_wire_strings() -> None:
    # _verify keeps the verification status; the strings themselves are _wire's.
    assert _verify.CORRELATION_ID_HEADER is _wire.CORRELATION_ID_HEADER
    assert _verify.LLM_PROXY_CLIENT_SECRET_HEADER is _wire.LLM_PROXY_CLIENT_SECRET_HEADER
    assert (
        _verify.ATTRIBUTION_APP_HEADER.placeholder == _wire.ATTRIBUTION_APP_HEADER_PLACEHOLDER
    )

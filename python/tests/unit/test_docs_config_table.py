"""The configuration page agrees with the field table (#727, §2.1).

``_FIELDS`` in ``donkey_kit.core.config`` is the one place that names each
field's env var, TOML key, parser and check. These tests pin the docs to it:
every table with an "Env var" and a "``DonkeyConfig`` field" column pairs them
as the table does and, between them, lists every field; a "Default" column
matches the dataclass default; the "Invalid values" choices match the checks;
and the "Precedence" list is :data:`_PRECEDENCE`, in order.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from donkey_kit.core import config as config_module
from donkey_kit.core.config import DonkeyConfig

_REPO = Path(__file__).resolve().parents[3]
_PAGE = _REPO / "website" / "content" / "reference" / "configuration.mdx"

pytestmark = pytest.mark.skipif(
    not _PAGE.is_file(), reason="the repository docs are not included in the sdist"
)

_CODE = re.compile(r"`([^`]+)`")
_CELL = re.compile(r"(?<!\\)\|")  # a cell border, not an escaped ``\|``


def _tables(text: str) -> list[list[list[str]]]:
    """Every Markdown table on the page, as rows of stripped cells (header
    first, the ``|---|`` separator dropped)."""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in [*text.splitlines(), ""]:
        if line.startswith("|"):
            cells = [cell.strip() for cell in _CELL.split(line.strip().strip("|"))]
            if not all(set(cell) <= {"-", ":"} for cell in cells):
                current.append(cells)
        elif current:
            tables.append(current)
            current = []
    return tables


def _code(cell: str) -> str | None:
    match = _CODE.fullmatch(cell)
    return match.group(1) if match else None


def _field_rows() -> list[dict[str, str]]:
    """Each row of a table with both an env-var and a field column, keyed by
    header (``env``, ``field`` and, where present, ``default``)."""
    rows: list[dict[str, str]] = []
    for table in _tables(_PAGE.read_text(encoding="utf-8")):
        header = [cell.lower() for cell in table[0]]
        if "`donkeyconfig` field" not in header:
            continue
        env_col = next((i for i, h in enumerate(header) if "env var" in h), None)
        if env_col is None:
            continue
        field_col = header.index("`donkeyconfig` field")
        default_col = header.index("default") if "default" in header else None
        for row in table[1:]:
            env, field = _code(row[env_col]), _code(row[field_col])
            if env is None or field is None:
                continue  # an environment-only switch such as DONKEY_NO_CACHE
            entry = {"env": env, "field": field}
            if default_col is not None:
                entry["default"] = row[default_col]
            rows.append(entry)
    return rows


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n#", start + len(heading))
    return text[start : end if end != -1 else None]


def test_the_table_covers_every_field_but_cost() -> None:
    public = {f.name for f in dataclasses.fields(DonkeyConfig) if not f.name.startswith("_")}
    assert {spec.name for spec in config_module._FIELDS} == public - {"cost"}
    assert len({spec.env for spec in config_module._FIELDS}) == len(config_module._FIELDS)


def test_docs_pair_each_env_var_with_its_field() -> None:
    table = {spec.name: spec.env for spec in config_module._FIELDS}
    rows = _field_rows()
    wrong = [f"{r['env']} -> {r['field']}" for r in rows if table.get(r["field"]) != r["env"]]
    assert not wrong, "the docs pair these env vars with the wrong field:\n" + "\n".join(wrong)
    missing = set(table) - {r["field"] for r in rows}
    assert not missing, f"configuration.mdx has no env-var row for: {sorted(missing)}"


def test_docs_defaults_match_the_dataclass() -> None:
    defaults = {
        f.name: f.default for f in dataclasses.fields(DonkeyConfig) if f.default is not None
    }
    wrong: list[str] = []
    for row in _field_rows():
        if "default" not in row or row["field"] not in defaults:
            continue
        value = defaults[row["field"]]
        expected = str(value).lower() if isinstance(value, bool) else str(value)
        if _code(row["default"]) != expected:
            wrong.append(f"{row['field']}: docs say {row['default']}, the default is {value!r}")
    assert not wrong, "\n".join(wrong)


def test_docs_list_the_accepted_choices() -> None:
    text = _PAGE.read_text(encoding="utf-8")
    invalid = _section(text, "### Invalid values")
    rows = {row[0]: row[1] for table in _tables(invalid) for row in table[1:]}
    for spec in config_module._FIELDS:
        if spec.check is None:
            continue
        expected = spec.check(object())
        assert expected is not None
        choices = re.findall(r"'([^']+)'", expected)
        if not choices:
            continue  # a range check, not a choice
        cell = rows[f"`{spec.name}`"]
        assert set(_CODE.findall(cell)) == set(choices), f"{spec.name}: {cell}"


def test_docs_precedence_follows_the_resolution_order() -> None:
    text = _PAGE.read_text(encoding="utf-8")
    items = re.findall(r"^\d+\. (.+)$", _section(text, "### Precedence"), re.MULTILINE)
    markers = {
        "explicit": "set in code",
        "env": "Environment variables",
        "local": ".donkey-kit.local.toml",
        "project": ".donkey-kit.toml",
        "user": "The user file",
        "default": "Defaults",
    }
    assert len(items) == len(config_module._PRECEDENCE)
    for kind, item in zip(config_module._PRECEDENCE, items, strict=True):
        assert item.startswith(("Values " + markers[kind], markers[kind], "`./" + markers[kind])), (
            f"{kind}: {item}"
        )

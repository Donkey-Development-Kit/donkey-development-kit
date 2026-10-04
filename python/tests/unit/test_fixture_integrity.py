"""The fixture-integrity lock (BG §1.4 honesty, #189).

A simulator that is *subtly* wrong is worse than none: it teaches an agent to
handle a shape production never sends. The "same files, both fail together"
rule (``classify()`` and the simulator read one set of fixtures) catches a
fixture edit that flips a discriminator — but a *benign* byte edit (reformatted
JSON, an added field, a whitespace tweak) leaves every assertion green while
silently drifting the bytes the simulator replays away from the captured shape.

This lock closes that gap. :data:`donkey_kit.simulator.fixtures.LOCK_PATH` pins
the sha256 of every fixture file the simulator serves; it is package data,
``src/donkey_kit/simulator/_fixtures/fixtures.lock`` in a source checkout (#746).
This test fails loudly the moment any of those bytes change. A legitimate
re-capture is accepted by regenerating the lock
(``python -m donkey_kit.simulator.fixtures --relock``) and committing it — the
deliberate, reviewable "I meant this" step. It is an integrity assertion, not
fixture-capture tooling (which #189 puts out of scope).

Base-only safe: imports only the framework-free ``simulator.fixtures`` module.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

from donkey_kit.simulator import fixtures


def test_served_fixtures_match_the_committed_lock() -> None:
    """Every simulator-served fixture's bytes match ``fixtures.lock``.

    If this fails, a fixture changed on disk without the lock being updated.
    """
    current = fixtures.compute_manifest()
    locked = fixtures.read_lock()

    assert current == locked, (
        f"A simulator fixture's bytes changed but {fixtures.LOCK_PATH} "
        "was not updated. If you re-captured a shape from a real gateway, run\n"
        "    python -m donkey_kit.simulator.fixtures --relock\n"
        "and commit the updated lock. If you did NOT intend to change a "
        "fixture, revert the edit — the simulator must replay captured bytes "
        "verbatim (BG §1.4). Drift:\n"
        f"  changed/added: {sorted(k for k in current if current.get(k) != locked.get(k))}\n"
        f"  removed:       {sorted(k for k in locked if k not in current)}"
    )


def test_lock_is_package_data_beside_the_fixtures() -> None:
    """The lock and the fixtures it pins are one package-data tree (#746), never
    a path into the repo's ``tests/`` directory."""
    package_dir = Path(fixtures.__file__).resolve().parent
    assert fixtures.LOCK_PATH == package_dir / "_fixtures" / "fixtures.lock"
    assert "tests" not in fixtures.LOCK_PATH.relative_to(package_dir).parts


def test_relock_cli_requires_a_source_checkout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The dev-only command cannot silently rewrite an installed wheel."""
    monkeypatch.setattr(fixtures, "_CHECKOUT_PYPROJECT", Path("/nonexistent/pyproject.toml"))
    monkeypatch.setattr("sys.argv", ["fixtures", "--relock"])

    with pytest.raises(SystemExit, match="2"):
        fixtures._main()

    assert "--relock must run from an editable source checkout" in capsys.readouterr().err


def test_lock_covers_exactly_the_served_files() -> None:
    """The lock has no stale rows and no gaps — its keys are exactly the files
    the shape table resolves to, so a newly added shape cannot ship unlocked."""
    expected_keys = {f"{directory}/{name}" for directory, name in fixtures._served_files()}
    assert set(fixtures.read_lock()) == expected_keys


def test_wheel_build_takes_nothing_from_the_test_tree() -> None:
    """Shipped runtime data is package data, never a ``force-include`` from
    ``tests/`` (#746)."""
    pyproject = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())
    wheel = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert "force-include" not in wheel
    assert "hooks" not in wheel

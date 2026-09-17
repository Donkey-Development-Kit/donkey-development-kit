"""The fixture-integrity lock (BG §1.4 honesty, #189).

A simulator that is *subtly* wrong is worse than none: it teaches an agent to
handle a shape production never sends. The "same files, both fail together"
rule (``classify()`` and the simulator read one set of fixtures) catches a
fixture edit that flips a discriminator — but a *benign* byte edit (reformatted
JSON, an added field, a whitespace tweak) leaves every assertion green while
silently drifting the bytes the simulator replays away from the captured shape.

This lock closes that gap. ``tests/fixtures/fixtures.lock`` pins the sha256 of
every fixture file the simulator serves; this test fails loudly the moment any
of those bytes change. A legitimate re-capture is accepted by regenerating the
lock (``python -m donkey_kit.simulator.fixtures --relock``) and committing it —
the deliberate, reviewable "I meant this" step. It is an integrity assertion,
not fixture-capture tooling (which #189 puts out of scope).

Base-only safe: imports only the framework-free ``simulator.fixtures`` module.
"""

from __future__ import annotations

from donkey_kit.simulator import fixtures


def test_served_fixtures_match_the_committed_lock() -> None:
    """Every simulator-served fixture's bytes match ``fixtures.lock``.

    If this fails, a fixture changed on disk without the lock being updated.
    """
    current = fixtures.compute_manifest()
    locked = fixtures.read_lock()

    assert current == locked, (
        "A simulator fixture's bytes changed but tests/fixtures/fixtures.lock "
        "was not updated. If you re-captured a shape from a real gateway, run\n"
        "    python -m donkey_kit.simulator.fixtures --relock\n"
        "and commit the updated lock. If you did NOT intend to change a "
        "fixture, revert the edit — the simulator must replay captured bytes "
        "verbatim (BG §1.4). Drift:\n"
        f"  changed/added: {sorted(k for k in current if current.get(k) != locked.get(k))}\n"
        f"  removed:       {sorted(k for k in locked if k not in current)}"
    )


def test_lock_covers_exactly_the_served_files() -> None:
    """The lock has no stale rows and no gaps — its keys are exactly the files
    the shape table resolves to, so a newly added shape cannot ship unlocked."""
    expected_keys = {f"{directory}/{name}" for directory, name in fixtures._served_files()}
    assert set(fixtures.read_lock()) == expected_keys

"""Cost-attribution tags (docs/verified-apis.md §3, BG §1.7, #196).

``CostTags`` is the pure, framework-free carrier for the FIXED four-dimension
attribution set. These tests pin the two guarantees the acceptance criteria
call out: the key set is fixed (an unknown key is a config error, not a
silently-dropped header) and values are validated so nobody can stuff JSON — or
a second header — into a tag value.
"""

from __future__ import annotations

import pytest

from donkey_kit.core.cost import CostTags
from donkey_kit.core.errors import ConfigError


# --- the fixed key set ------------------------------------------------------
def test_from_mapping_accepts_the_four_fixed_keys() -> None:
    tags = CostTags.from_mapping(
        {"team": "support", "project": "triage-v2", "env": "prod", "enduser.id": "u-1"}
    )
    assert tags.team == "support"
    assert tags.project == "triage-v2"
    assert tags.env == "prod"
    assert tags.enduser_id == "u-1"


def test_from_mapping_rejects_an_unknown_key() -> None:
    # AC #1: an unknown key is a CONFIG ERROR, never a silently-dropped header.
    with pytest.raises(ConfigError) as exc:
        CostTags.from_mapping({"team": "support", "region": "us"})
    assert "region" in str(exc.value)


def test_from_mapping_reports_every_unknown_key_at_once() -> None:
    with pytest.raises(ConfigError) as exc:
        CostTags.from_mapping({"foo": "a", "bar": "b"})
    message = str(exc.value)
    assert "foo" in message
    assert "bar" in message


def test_empty_mapping_is_the_empty_tags() -> None:
    assert CostTags.from_mapping({}).is_empty
    assert CostTags().is_empty
    assert not CostTags(team="x").is_empty


# --- value validation (AC #2) -----------------------------------------------
@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\tb", "a\x00b", "a\x7fb"])
def test_control_characters_are_rejected(bad: str) -> None:
    # A newline in a header value is header injection; a control char has no
    # place in an attribution tag either. Reject at construction.
    with pytest.raises(ConfigError):
        CostTags(team=bad)


def test_overlong_value_is_rejected() -> None:
    with pytest.raises(ConfigError):
        CostTags(project="x" * 257)


def test_max_length_value_is_accepted() -> None:
    assert CostTags(project="x" * 256).project == "x" * 256


def test_empty_string_value_is_rejected() -> None:
    # A tag set to "" is a config mistake, not "no tag" — that is ``None``.
    with pytest.raises(ConfigError):
        CostTags(team="")


def test_from_mapping_validates_values_too() -> None:
    with pytest.raises(ConfigError):
        CostTags.from_mapping({"team": "a\nb"})


# --- merge (run-scope over config) ------------------------------------------
def test_merge_prefers_the_other_set_fields_per_field() -> None:
    base = CostTags(team="support", project="triage-v2", env="prod")
    override = CostTags(project="triage-v3", enduser_id="u-9")
    merged = base.merge(override)
    assert merged.team == "support"        # kept from base
    assert merged.project == "triage-v3"   # overridden
    assert merged.env == "prod"            # kept from base
    assert merged.enduser_id == "u-9"      # added by override


def test_merge_with_empty_is_identity() -> None:
    base = CostTags(team="support", env="prod")
    assert base.merge(CostTags()) == base
    assert CostTags().merge(base) == base


# --- iteration surfaces used by transport + telemetry -----------------------
def test_items_yields_only_set_fields_as_field_value_pairs() -> None:
    tags = CostTags(team="support", enduser_id="u-1")
    assert dict(tags.items()) == {"team": "support", "enduser_id": "u-1"}


def test_span_kwargs_are_the_prefixed_field_names() -> None:
    tags = CostTags(team="support", project="p", env="e", enduser_id="u")
    assert tags.span_kwargs() == {
        "cost_team": "support",
        "cost_project": "p",
        "cost_env": "e",
        "cost_enduser_id": "u",
    }


def test_span_kwargs_omit_unset_fields() -> None:
    assert CostTags(team="support").span_kwargs() == {"cost_team": "support"}

"""Mapping strings, table aliases, and the row augmentations."""

from __future__ import annotations

from typing import Any

import pytest

from forward_infrahub.exceptions import InvalidConfiguration
from forward_infrahub.settings import AdapterSettings
from forward_infrahub.tables import (
    TABLES,
    AugmentContext,
    augment_devices,
    augment_locations,
    augment_namespaces,
    parse_mapping,
)


def test_bundled_mapping_resolves_to_a_table() -> None:
    ref = parse_mapping("nqe:devices")
    assert ref.bundled
    assert ref.spec is not None
    assert ref.spec.query_files == ("forward_devices.nqe",)


@pytest.mark.parametrize(
    "mapping",
    ["nqe-path:/org/queries/mine", "nqe-id:FQ_abc", "nqe-id:FQ_abc@commit", "nqe-file:./mine.nqe"],
)
def test_escape_hatches_parse(mapping: str) -> None:
    ref = parse_mapping(mapping)
    assert not ref.bundled
    assert ref.spec is None


@pytest.mark.parametrize(
    ("mapping", "match"),
    [
        ("", "must look like"),
        ("devices", "must look like"),
        ("nqe:", "must look like"),
        ("nqe:nonsense", "unknown bundled table"),
        ("rest:devices", "not supported yet"),
        ("http:example", "unknown mapping scheme"),
    ],
)
def test_bad_mappings_say_what_is_wrong(mapping: str, match: str) -> None:
    with pytest.raises(InvalidConfiguration, match=match):
        parse_mapping(mapping)


def test_an_alias_shares_the_underlying_query() -> None:
    """VRFs and namespaces read one query, so it must run only once."""
    assert TABLES["ip_namespaces"].query_files == TABLES["vrfs"].query_files
    assert parse_mapping("nqe:ip_namespaces").execution_keys == (
        parse_mapping("nqe:vrfs").execution_keys
    )


def test_prefixes_span_both_address_families() -> None:
    keys = parse_mapping("nqe:prefixes").execution_keys
    assert len(keys) == 2
    assert any("ipv4" in key for key in keys)
    assert any("ipv6" in key for key in keys)


def ctx(**kwargs: Any) -> AugmentContext:
    return AugmentContext(settings=AdapterSettings(**kwargs))


def test_location_variants_collapse_and_keep_the_first_name() -> None:
    rows = [
        {"name": "120 W ELM ST", "scope_devices": ["a"]},
        {"name": "120 W Elm Street", "scope_devices": ["b"]},
    ]
    context = ctx()
    out = augment_locations(rows, context)
    assert [row["name"] for row in out] == ["120 W ELM ST"]
    assert out[0]["scope_devices"] == ["a", "b"]
    assert context.location_aliases == {"120 W Elm Street": "120 W ELM ST"}


def test_collapsing_can_be_turned_off() -> None:
    rows = [{"name": "120 W ELM ST"}, {"name": "120 W Elm Street"}]
    out = augment_locations(rows, ctx(normalize_location_names=False))
    assert len(out) == 2


def test_genuinely_different_addresses_stay_apart() -> None:
    rows = [{"name": "10 W ELM ST"}, {"name": "12 W ELM ST"}]
    assert len(augment_locations(rows, ctx())) == 2


def test_devices_follow_the_collapsed_site_name() -> None:
    context = ctx()
    augment_locations([{"name": "120 W ELM ST"}, {"name": "120 W Elm Street"}], context)
    out = augment_devices([{"name": "d1", "location": "120 W Elm Street"}], context)
    assert out[0]["location"] == "120 W ELM ST"


def test_an_unassigned_site_is_added_only_when_named() -> None:
    assert augment_locations([], ctx()) == []
    out = augment_locations([], ctx(unassigned_location_name="Unknown"))
    assert [row["name"] for row in out] == ["Unknown"]


def test_devices_without_a_location_can_be_parked() -> None:
    context = ctx(unassigned_location_name="Unknown")
    out = augment_devices([{"name": "d1", "location": ""}], context)
    assert out[0]["location"] == "Unknown"


def test_namespaces_always_include_the_default() -> None:
    out = augment_namespaces([{"name": "BLUE", "scope_devices": ["a"]}], ctx())
    assert sorted(row["name"] for row in out) == ["BLUE", "default"]


def test_namespaces_are_deduplicated() -> None:
    rows = [{"name": "BLUE"}, {"name": "BLUE"}, {"name": "default"}]
    assert len(augment_namespaces(rows, ctx())) == 2

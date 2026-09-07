"""Comparing this sync's idea of an object's identity with Infrahub's."""

from __future__ import annotations

from typing import Any

from forward_infrahub.identity import check_identities

SCHEMA: dict[str, Any] = {
    "generics": [
        {"kind": "IpamGenericVLAN", "human_friendly_id": ["name__value"]},
        {"kind": "DcimGenericDevice", "human_friendly_id": ["name__value"]},
    ],
    "nodes": [
        # The real shape that lost 70 VLANs: identified by name, scoped by group.
        {
            "kind": "IpamVLAN",
            "inherit_from": ["IpamGenericVLAN"],
            "uniqueness_constraints": [["vlan_id__value", "vlan_group"]],
        },
        {"kind": "InterfacePhysical", "human_friendly_id": ["device__name__value", "name__value"]},
        {"kind": "DcimDevice", "inherit_from": ["DcimGenericDevice"]},
        {"kind": "CloudVPC", "uniqueness_constraints": [["account", "network_id__value"]]},
    ],
}


def check(kind: str, **element: Any) -> Any:
    return check_identities(SCHEMA, [{"name": kind, **element}])[0]


def test_matching_identifiers_are_fine() -> None:
    result = check("InterfacePhysical", identifiers=["device", "name"])
    assert not result.merges
    assert result.summary.endswith("ok")


def test_an_identifier_the_id_cannot_see_is_a_merge() -> None:
    """The VLAN case: two rows differing only by scope become one node."""
    result = check(
        "IpamVLAN",
        identifiers=["vlan_id", "vlan_group"],
        fields=[{"name": "name", "mapping": "name"}],
    )
    assert result.merges
    assert result.unrepresented == ("vlan_group", "vlan_id")
    assert "written over each other" in result.summary


def test_an_inherited_id_is_found_and_named() -> None:
    result = check("IpamVLAN", identifiers=["vlan_id", "vlan_group"])
    assert "inherited from IpamGenericVLAN" in result.summary


def test_a_transform_that_carries_the_identifiers_clears_it() -> None:
    """The fix: the name in the id is built from the columns that identify it."""
    result = check(
        "IpamVLAN",
        identifiers=["vlan_id", "vlan_group"],
        transforms=[
            {"field": "qualified_name", "expression": "{{ name }} ({{ vid }}) @ {{ site }}"}
        ],
        fields=[
            {"name": "name", "mapping": "qualified_name"},
            {"name": "vlan_id", "mapping": "vid"},
            {"name": "vlan_group", "mapping": "site"},
        ],
    )
    assert not result.merges
    assert result.carried_by_transform == ("vlan_group", "vlan_id")


def test_a_transform_that_carries_only_one_still_merges() -> None:
    """Half a qualification is still a collision, just a rarer one."""
    result = check(
        "IpamVLAN",
        identifiers=["vlan_id", "vlan_group"],
        transforms=[{"field": "qualified_name", "expression": "{{ name }} ({{ vid }})"}],
        fields=[
            {"name": "name", "mapping": "qualified_name"},
            {"name": "vlan_id", "mapping": "vid"},
            {"name": "vlan_group", "mapping": "site"},
        ],
    )
    assert result.merges
    assert result.unrepresented == ("vlan_group",)


def test_a_kind_with_no_id_relies_on_its_constraints() -> None:
    """Without a human-friendly id an upsert falls back to uniqueness."""
    result = check("CloudVPC", identifiers=["account", "network_id"])
    assert not result.merges
    assert "uniqueness constraints" in result.summary


def test_every_mapped_kind_is_reported() -> None:
    results = check_identities(
        SCHEMA,
        [
            {"name": "DcimDevice", "identifiers": ["name"]},
            {"name": "InterfacePhysical", "identifiers": ["device", "name"]},
        ],
    )
    assert [r.kind for r in results] == ["DcimDevice", "InterfacePhysical"]

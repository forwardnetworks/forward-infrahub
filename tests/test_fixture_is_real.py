"""The fixture must assert things Forward could actually return.

A fixture built on a value the real system cannot produce is not a weak test,
it is an absent one: nothing it does can be wrong, so it keeps passing while it
drifts. These checks tie the enum-derived columns back to the enums the SDK
generates from Forward's own API description, which is the nearest thing to
reality available offline.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from forward_sdk.models import DeviceType, Vendor, VendorOs

from .conftest import FIXTURE

#: Column to the enum whose members it is drawn from. ``toString`` renders an
#: enum as ``Type.MEMBER``, so a prefix is stripped before comparing.
ENUM_COLUMNS: list[tuple[str, str, Any]] = [
    ("forward_manufacturers", "name", Vendor),
    ("forward_platforms", "name", VendorOs),
    ("forward_platforms", "manufacturer", Vendor),
    ("forward_device_types", "manufacturer", Vendor),
    ("forward_devices", "vendor", Vendor),
    ("forward_devices", "device_type", DeviceType),
    ("forward_devices", "platform", VendorOs),
]


@pytest.fixture(scope="module")
def rows() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return loaded


def member_names(enum_cls: Any) -> set[str]:
    return {str(member.value).casefold() for member in enum_cls}


@pytest.mark.parametrize(("table", "column", "enum_cls"), ENUM_COLUMNS)
def test_enum_columns_hold_real_members(
    rows: dict[str, Any], table: str, column: str, enum_cls: Any
) -> None:
    known = member_names(enum_cls)
    for row in rows[table]:
        value = str(row[column])
        # Strip the Type. prefix that toString emits, where the query keeps it.
        bare = value.rsplit(".", 1)[-1]
        assert bare.casefold() in known, (
            f"{table}.{column} is {value!r}, which is not a {enum_cls.__name__} "
            "Forward can return, so any test reading it proves nothing"
        )


def test_the_device_description_matches_what_the_query_builds(rows: dict[str, Any]) -> None:
    """The query joins four columns; a fixture that disagrees has drifted."""
    for row in rows["forward_devices"]:
        expected = " ".join([row["vendor"], row["model"], row["platform"], row["os_version"]])
        assert row["description"] == expected


def test_derived_keys_match_their_parts(rows: dict[str, Any]) -> None:
    """Reference keys are built by the queries, so they cannot be written freehand."""
    for row in rows["forward_ip_addresses"]:
        assert row["interface_key"] == f"{row['device']}__{row['interface']}"
    for row in rows["forward_cloud_networks"]:
        assert row["account_key"] == f"{row['cloud_type']}__{row['account_id']}"
        expected_vpc = f"{row['account_key']}__{row['parent_id']}" if row["parent_id"] else ""
        assert row["vpc_key"] == expected_vpc
    for row in rows["forward_cables"]:
        assert row["connected_endpoints"] == [
            f"{row['device']}__{row['interface']}",
            f"{row['remote_device']}__{row['remote_interface']}",
        ]


def test_the_vrf_reference_is_empty_only_in_the_default_namespace(rows: dict[str, Any]) -> None:
    for table in ("forward_prefixes_ipv4", "forward_prefixes_ipv6", "forward_ip_addresses"):
        for row in rows[table]:
            expected = "" if row["namespace"] == "default" else row["namespace"]
            assert row["vrf_ref"] == expected

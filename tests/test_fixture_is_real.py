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

from forward_infrahub.nqe_types import DEVICE_TYPE, OS, VENDOR, nqe_members

from .conftest import FIXTURE

#: Column to the NQE type whose members it is drawn from. ``toString`` renders
#: an enum as ``Type.MEMBER``, so a prefix is stripped before comparing.
#:
#: These are NQE types, not the SDK enums of the same name. The SDK models the
#: REST schema, which for DeviceType is a different set entirely, so checking a
#: fixture against it would have asked whether the rows are plausible in a
#: namespace they never came from.
ENUM_COLUMNS: list[tuple[str, str, str]] = [
    ("forward_manufacturers", "name", VENDOR),
    ("forward_platforms", "name", OS),
    ("forward_platforms", "manufacturer", VENDOR),
    ("forward_device_types", "manufacturer", VENDOR),
    ("forward_devices", "vendor", VENDOR),
    ("forward_devices", "device_type", DEVICE_TYPE),
    ("forward_devices", "platform", OS),
]


@pytest.fixture(scope="module")
def rows() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return loaded


@pytest.mark.parametrize(("table", "column", "enum_type"), ENUM_COLUMNS)
def test_enum_columns_hold_real_members(
    rows: dict[str, Any], table: str, column: str, enum_type: str
) -> None:
    known = {member.casefold() for member in nqe_members(enum_type)}
    for row in rows[table]:
        value = str(row[column])
        # Strip the Type. prefix that toString emits, where the query keeps it.
        bare = value.rsplit(".", 1)[-1]
        assert bare.casefold() in known, (
            f"{table}.{column} is {value!r}, which is not an NQE {enum_type} "
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

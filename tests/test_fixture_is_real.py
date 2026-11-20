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
    """The query joins four columns, vendor minus its enum prefix; a stale fixture drifted."""
    for row in rows["forward_devices"]:
        vendor = row["vendor"].replace("Vendor.", "")
        expected = " ".join([vendor, row["model"], row["platform"], row["os_version"]])
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


def test_a_software_releases_name_is_its_platform_and_version(rows: dict[str, Any]) -> None:
    """The query builds the name from the two, so a fixture that disagrees has drifted."""
    for row in rows["forward_software_versions"]:
        assert row["name"] == f"{row['platform']} {row['version']}"


def test_every_version_a_device_reports_has_a_release_row(rows: dict[str, Any]) -> None:
    """The query makes one row per OS family and version a collected device runs."""
    wanted = {f"{d['platform']} {d['os_version']}" for d in rows["forward_devices"]}
    present = {row["name"] for row in rows["forward_software_versions"]}
    assert wanted <= present


def test_a_vulnerabilitys_severity_is_an_nqe_severity(rows: dict[str, Any]) -> None:
    """The query strips the ``Severity.`` prefix, so a row carries the bare member."""
    from forward_infrahub.nqe_types import nqe_members

    known = nqe_members("Severity")
    assert known, "the SDK no longer describes the Severity type"
    for row in rows["forward_vulnerabilities"]:
        assert row["severity"] in known


def test_every_severity_the_query_can_emit_is_a_choice_in_the_schema() -> None:
    """NQE's Severity members, lower-cased, plus the query's own `unknown`, are the dropdown."""
    from pathlib import Path

    import yaml

    schema = yaml.safe_load(
        (
            Path(__file__).parent.parent / "examples/forwardnetworks_to_infrahub/schema.yml"
        ).read_text()
    )
    node = next(n for n in schema["nodes"] if n["name"] == "Vulnerability")
    attribute = next(a for a in node["attributes"] if a["name"] == "severity")
    choices = {c["name"] for c in attribute["choices"]}

    emitted = {member.lower() for member in nqe_members("Severity")} | {"unknown"}

    assert emitted <= choices, f"the dropdown lacks {sorted(emitted - choices)}"

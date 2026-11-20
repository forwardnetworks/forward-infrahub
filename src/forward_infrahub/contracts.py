"""Row contracts for the bundled queries.

Each bundled ``.nqe`` file produces a fixed set of columns. The adapter, the
example ``config.yml`` and the tests all rely on these names, so they are
declared once here and checked against the query source by
``forward_sdk.nqe.files.select_field_sets``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from forward_sdk.nqe.files import contract_version, missing_fields, select_field_sets

CONTRACT_VERSION = "v3-infrahub"

PRIMARY_KEY_RE = re.compile(r"^[ \t]*@primaryKey\(([^)]*)\)", re.MULTILINE)

QUERY_CONTRACT_FIELDS: dict[str, tuple[str, ...]] = {
    "forward_manufacturers.nqe": ("name", "scope_devices"),
    "forward_platforms.nqe": ("name", "manufacturer", "scope_devices"),
    "forward_device_types.nqe": ("manufacturer", "name", "scope_devices"),
    "forward_locations.nqe": (
        "name",
        "city",
        "country",
        "description",
        "physical_address",
        "scope_devices",
    ),
    "forward_devices.nqe": (
        "name",
        "location",
        "vendor",
        "device_type",
        "model",
        "platform",
        "os_version",
        "serial_number",
        "tags",
        "description",
    ),
    "forward_interfaces.nqe": (
        "device",
        "name",
        "kind",
        "type",
        "site",
        "lag",
        "lag_key",
        "lag_number",
        "mode",
        "untagged_vid",
        "tagged_vids",
        "admin_up",
        "oper_up",
        "mtu",
        "description",
        "speed",
        "mac_address",
    ),
    "forward_vrfs.nqe": ("name", "scope_devices"),
    "forward_vulnerabilities.nqe": (
        "cve_id",
        "severity",
        "published",
        "cisa_kev",
        "advisory_url",
        "scope_devices",
    ),
    "forward_software_versions.nqe": (
        "name",
        "platform",
        "version",
        "end_of_maintenance",
        "end_of_vulnerability_support",
        "end_of_support",
        "announcement_url",
        "scope_devices",
    ),
    "forward_vlans.nqe": ("site", "vid", "name", "status", "scope_devices"),
    "forward_prefixes_ipv4.nqe": ("namespace", "vrf_ref", "prefix", "family", "status"),
    "forward_prefixes_ipv6.nqe": ("namespace", "vrf_ref", "prefix", "family", "status"),
    "forward_ip_addresses.nqe": (
        "device",
        "interface",
        "interface_kind",
        "interface_key",
        "namespace",
        "vrf_ref",
        "address",
        "host_ip",
        "prefix_length",
        "description",
        "primary_of",
        "status",
    ),
    "forward_cables.nqe": (
        "device",
        "interface",
        "remote_device",
        "remote_interface",
        "label",
        "connected_endpoints",
        "status",
    ),
    "forward_cloud_accounts.nqe": ("account_id", "name", "cloud_type"),
    "forward_cloud_networks.nqe": (
        "account_id",
        "cloud_type",
        "account_key",
        "network_id",
        "name",
        "parent_id",
        "vpc_key",
        "kind",
        "cidrs",
    ),
    "forward_cloud_services.nqe": (
        "account_id",
        "cloud_type",
        "account_key",
        "service_id",
        "name",
        "vpc_id",
        "vpc_key",
        "service_kind",
    ),
}


def primary_key_columns(source: str) -> tuple[str, ...]:
    """The columns named by the query's ``@primaryKey`` annotation."""
    match = PRIMARY_KEY_RE.search(source)
    if not match:
        return ()
    return tuple(part.strip() for part in match.group(1).split(",") if part.strip())


def contract_problems(filename: str, source: str) -> list[str]:
    """Everything wrong with one bundled query, as human-readable strings."""
    problems: list[str] = []
    expected = QUERY_CONTRACT_FIELDS.get(filename)
    if expected is None:
        return [f"{filename}: not in QUERY_CONTRACT_FIELDS"]
    keys = PRIMARY_KEY_RE.findall(source)
    if len(keys) != 1:
        problems.append(f"{filename}: expected exactly one @primaryKey, found {len(keys)}")
    else:
        pk = primary_key_columns(source)
        unknown = [c for c in pk if c not in expected]
        if unknown:
            problems.append(f"{filename}: @primaryKey names non-contract columns {unknown}")
    if re.search(r"^\s*@query\b", source, re.MULTILINE):
        problems.append(f"{filename}: parameterised (@query) queries cannot be diffed")
    if source.rstrip().endswith(";"):
        problems.append(f"{filename}: must not end with ';'")
    if contract_version(source) != CONTRACT_VERSION:
        problems.append(
            f"{filename}: @contract-version {contract_version(source)!r} != {CONTRACT_VERSION!r}"
        )
    missing = missing_fields(source, expected)
    if missing:
        problems.append(f"{filename}: no select block produces {list(missing)}")
    if not any(set(expected) <= set(block) for block in select_field_sets(source)):
        problems.append(f"{filename}: no single select block produces every contract column")
    return problems


def check_contracts(sources: Iterable[tuple[str, str]]) -> list[str]:
    """Run :func:`contract_problems` over ``(filename, source)`` pairs."""
    problems: list[str] = []
    for filename, source in sources:
        problems.extend(contract_problems(filename, source))
    return problems

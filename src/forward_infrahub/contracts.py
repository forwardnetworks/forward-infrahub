"""Row contracts for the bundled queries.

Each bundled ``.nqe`` file produces a fixed set of columns. The adapter, the
example ``config.yml`` and the tests all rely on these names, so they are
declared once here and checked against the query source by a ``QueryContractReader``,
which reads a query's contract version and select blocks. The reader comes from outside
(an adapter backed by the Forward SDK), so this module imports nothing from the SDK.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

__all__ = [
    "QUERY_CONTRACT_FIELDS",
    "QUERY_FILENAMES",
    "QueryContractReader",
    "check_contracts",
    "library_path",
    "primary_key_columns",
    "query_stem",
]

CONTRACT_VERSION = "v3-infrahub"


@runtime_checkable
class QueryContractReader(Protocol):
    """Reads what a query's source declares and produces."""

    def contract_version(self, source: str) -> str | None:
        """The ``@contract-version`` recorded in the source, if any."""
        ...

    def missing_fields(self, source: str, expected: Sequence[str]) -> Sequence[str]:
        """The expected column names that no ``select`` block produces."""
        ...

    def select_field_sets(self, source: str) -> Sequence[Sequence[str]]:
        """The field names of each ``select { ... }`` block."""
        ...


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


def contract_problems(filename: str, source: str, reader: QueryContractReader) -> list[str]:
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
    version = reader.contract_version(source)
    if version != CONTRACT_VERSION:
        problems.append(f"{filename}: @contract-version {version!r} != {CONTRACT_VERSION!r}")
    missing = reader.missing_fields(source, expected)
    if missing:
        problems.append(f"{filename}: no select block produces {list(missing)}")
    if not any(set(expected) <= set(block) for block in reader.select_field_sets(source)):
        problems.append(f"{filename}: no single select block produces every contract column")
    return problems


def check_contracts(sources: Iterable[tuple[str, str]], reader: QueryContractReader) -> list[str]:
    """Run :func:`contract_problems` over ``(filename, source)`` pairs."""
    problems: list[str] = []
    for filename, source in sources:
        problems.extend(contract_problems(filename, source, reader))
    return problems


#: Every bundled query file, in a stable order.
QUERY_FILENAMES: tuple[str, ...] = tuple(sorted(QUERY_CONTRACT_FIELDS))


def query_stem(filename: str) -> str:
    return filename[: -len(".nqe")] if filename.endswith(".nqe") else filename


def library_path(directory: str, filename: str) -> str:
    """Where a bundled query lives in the org repository once published."""
    return f"{directory.rstrip('/')}/{query_stem(filename)}"

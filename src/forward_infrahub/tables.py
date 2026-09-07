"""Virtual tables: how a ``mapping:`` string in ``config.yml`` becomes rows.

A bundled table (``nqe:devices``) is backed by one or more of the shipped
queries, knows how it relates to devices for scoping, and may be augmented
after fetch. Escape hatches let an operator point a kind at a query of their
own by library path, id, or file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from forward_sdk import ForwardConfigurationError

from forward_infrahub.normalize import normalize_location_key
from forward_infrahub.settings import AdapterSettings

Row = dict[str, Any]
ScopeKind = Literal["name", "device", "scope_devices", "cloud", "none"]
Scheme = Literal["nqe", "nqe-path", "nqe-id", "nqe-file"]

#: How the adapter expands a reference to a generic kind: the concrete kinds it
#: searches, in order, for the referenced unique id.
GENERIC_PEERS: dict[str, tuple[str, ...]] = {
    "InterfaceLayer3": ("InterfacePhysical", "InterfaceVirtual"),
    "InterfaceLayer2": ("InterfacePhysical", "InterfaceVirtual"),
    "DcimInterface": ("InterfacePhysical", "InterfaceVirtual"),
    "DcimEndpoint": ("InterfacePhysical",),
    "DcimGenericDevice": ("DcimDevice",),
    "DcimPhysicalDevice": ("DcimDevice",),
    "LocationGeneric": ("LocationSite",),
    "LocationHosting": ("LocationSite",),
    "IpamPrefixScope": ("LocationSite",),
    "IpamVLANGroupScope": ("LocationSite",),
    "OrganizationGeneric": ("OrganizationManufacturer",),
}


@dataclass
class AugmentContext:
    """State shared between augmentations within one load."""

    settings: AdapterSettings
    location_aliases: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


Augmentation = Callable[[list[Row], AugmentContext], list[Row]]


@dataclass(frozen=True)
class TableSpec:
    name: str
    query_files: tuple[str, ...]
    scope: ScopeKind
    augment: Augmentation | None = None

    @property
    def scope_column(self) -> str | None:
        return {"name": "name", "device": "device", "scope_devices": "scope_devices"}.get(
            self.scope
        )


def _empty_scope_row(name: str) -> Row:
    return {
        "name": name,
        "city": "",
        "country": "",
        "description": "",
        "physical_address": "",
        "scope_devices": [],
    }


def augment_locations(rows: list[Row], ctx: AugmentContext) -> list[Row]:
    """Collapse formatting variants of one site and add the unassigned site if configured."""
    out: list[Row] = []
    if ctx.settings.normalize_location_names:
        canonical: dict[str, Row] = {}
        for row in rows:
            name = str(row.get("name") or "").strip()
            key = normalize_location_key(name)
            first = canonical.get(key)
            if first is None:
                canonical[key] = dict(row)
                out.append(canonical[key])
                continue
            ctx.location_aliases[name] = str(first["name"])
            merged = list(first.get("scope_devices") or []) + list(row.get("scope_devices") or [])
            first["scope_devices"] = sorted(set(merged))
    else:
        out = [dict(row) for row in rows]
    if ctx.location_aliases:
        ctx.notes.append(f"locations: collapsed {len(ctx.location_aliases)} name variant(s)")
    unassigned = ctx.settings.unassigned_location_name.strip()
    if unassigned and all(str(r.get("name")) != unassigned for r in out):
        out.append(_empty_scope_row(unassigned))
    return out


def _rewrite_location(row: Row, column: str, ctx: AugmentContext) -> Row:
    row = dict(row)
    value = str(row.get(column) or "").strip()
    if value in ctx.location_aliases:
        row[column] = ctx.location_aliases[value]
    elif not value and ctx.settings.unassigned_location_name.strip():
        row[column] = ctx.settings.unassigned_location_name.strip()
    return row


def augment_devices(rows: list[Row], ctx: AugmentContext) -> list[Row]:
    return [_rewrite_location(row, "location", ctx) for row in rows]


def augment_vlans(rows: list[Row], ctx: AugmentContext) -> list[Row]:
    return [_rewrite_location(row, "site", ctx) for row in rows]


def augment_namespaces(rows: list[Row], ctx: AugmentContext) -> list[Row]:
    """One namespace per VRF name, plus the built-in ``default`` namespace."""
    seen: dict[str, Row] = {}
    for row in rows:
        name = str(row.get("name") or "").strip()
        if name and name not in seen:
            seen[name] = {"name": name, "scope_devices": list(row.get("scope_devices") or [])}
    seen.setdefault("default", {"name": "default", "scope_devices": []})
    return list(seen.values())


TABLES: dict[str, TableSpec] = {
    spec.name: spec
    for spec in (
        TableSpec("manufacturers", ("forward_manufacturers.nqe",), "scope_devices"),
        TableSpec("platforms", ("forward_platforms.nqe",), "scope_devices"),
        TableSpec("device_types", ("forward_device_types.nqe",), "scope_devices"),
        TableSpec("locations", ("forward_locations.nqe",), "scope_devices", augment_locations),
        TableSpec("devices", ("forward_devices.nqe",), "name", augment_devices),
        TableSpec("interfaces", ("forward_interfaces.nqe",), "device"),
        TableSpec("vrfs", ("forward_vrfs.nqe",), "scope_devices"),
        TableSpec("ip_namespaces", ("forward_vrfs.nqe",), "scope_devices", augment_namespaces),
        TableSpec("vlans", ("forward_vlans.nqe",), "scope_devices", augment_vlans),
        TableSpec("prefixes_ipv4", ("forward_prefixes_ipv4.nqe",), "none"),
        TableSpec("prefixes_ipv6", ("forward_prefixes_ipv6.nqe",), "none"),
        TableSpec("prefixes", ("forward_prefixes_ipv4.nqe", "forward_prefixes_ipv6.nqe"), "none"),
        TableSpec("ip_addresses", ("forward_ip_addresses.nqe",), "device"),
        TableSpec("cables", ("forward_cables.nqe",), "device"),
        TableSpec("cloud_accounts", ("forward_cloud_accounts.nqe",), "cloud"),
        TableSpec("cloud_networks", ("forward_cloud_networks.nqe",), "cloud"),
        TableSpec("cloud_services", ("forward_cloud_services.nqe",), "cloud"),
    )
}

#: Tables whose rows must exist before scoping can be computed.
DEVICE_SCOPE_FILE = "forward_devices.nqe"
CLOUD_SCOPE_FILE = "forward_cloud_accounts.nqe"


@dataclass(frozen=True)
class TableRef:
    """A parsed ``mapping:`` value."""

    scheme: Scheme
    key: str
    spec: TableSpec | None = None

    @property
    def bundled(self) -> bool:
        return self.scheme == "nqe"

    @property
    def execution_keys(self) -> tuple[str, ...]:
        """One key per query execution this table needs."""
        if self.spec is not None:
            return tuple(f"file:{name}" for name in self.spec.query_files)
        return (f"{self.scheme}:{self.key}",)

    def __str__(self) -> str:
        return f"{self.scheme}:{self.key}"


def parse_mapping(mapping: str | None) -> TableRef:
    text = str(mapping or "").strip()
    scheme, sep, key = text.partition(":")
    key = key.strip()
    if not sep or not key:
        raise ForwardConfigurationError(
            f"mapping {text!r} must look like 'nqe:<table>', 'nqe-path:/dir/query', "
            "'nqe-id:FQ_...' or 'nqe-file:path.nqe'"
        )
    if scheme == "nqe":
        spec = TABLES.get(key)
        if spec is None:
            raise ForwardConfigurationError(
                f"unknown bundled table {key!r}; choose one of {', '.join(sorted(TABLES))}"
            )
        return TableRef("nqe", key, spec)
    if scheme in {"nqe-path", "nqe-id", "nqe-file"}:
        return TableRef(scheme, key)  # type: ignore[arg-type]
    if scheme == "rest":
        raise ForwardConfigurationError("rest: tables are not supported yet; use an nqe: table")
    raise ForwardConfigurationError(f"unknown mapping scheme {scheme!r} in {text!r}")


def query_files_for(refs: Iterable[TableRef]) -> set[str]:
    return {name for ref in refs if ref.spec is not None for name in ref.spec.query_files}

"""Ownership scope: which Forward devices and cloud accounts a sync speaks for.

Ported from the Forward Nautobot app. The allowlists are exact sets; values
within one list are OR'd, populated lists are AND'd. Matching is
case-insensitive and accepts either a rendered Forward enum (``Vendor.CISCO``)
or its final token (``cisco``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

Row = dict[str, Any]


def scope_token(value: Any) -> str:
    """Normalise Forward enum strings and operator values for scope comparison."""
    return str(value or "").strip().rsplit(".", 1)[-1].casefold()


@dataclass(frozen=True, slots=True)
class ForwardDeviceScope:
    """Current-snapshot device membership used to scope every device-derived table."""

    device_names: frozenset[str] = frozenset()

    @classmethod
    def from_device_rows(
        cls,
        rows: Iterable[Row],
        *,
        vendors: Sequence[str] = (),
        types: Sequence[str] = (),
        models: Sequence[str] = (),
    ) -> ForwardDeviceScope:
        allowed_vendors = {scope_token(v) for v in vendors if str(v).strip()}
        allowed_types = {scope_token(v) for v in types if str(v).strip()}
        allowed_models = {scope_token(v) for v in models if str(v).strip()}
        selected: set[str] = set()
        for row in rows:
            if allowed_vendors and scope_token(row.get("vendor")) not in allowed_vendors:
                continue
            if allowed_types and scope_token(row.get("device_type")) not in allowed_types:
                continue
            if allowed_models and scope_token(row.get("model")) not in allowed_models:
                continue
            name = str(row.get("name") or "").strip()
            if name:
                selected.add(name)
        return cls(device_names=frozenset(selected))

    def includes(self, row: Mapping[str, Any], *, column: str | None) -> bool:
        """Whether ``row`` belongs to the selected device closure.

        ``column`` names how the table relates to devices: ``"name"`` for the
        device table itself, ``"scope_devices"`` for aggregated tables that
        list contributing devices, ``"device"`` for per-device tables, and
        ``None`` for tables with no device relation, which fail closed.
        """
        if column is None:
            return False
        value = row.get(column)
        if isinstance(value, (list, tuple, set, frozenset)):
            return any(str(name or "").strip() in self.device_names for name in value)
        return str(value or "").strip() in self.device_names


def cloud_account_key(row: Mapping[str, Any]) -> tuple[str, str]:
    """Provider-qualified account identity for a cloud row."""
    return (scope_token(row.get("cloud_type")), str(row.get("account_id") or "").strip())


@dataclass(frozen=True, slots=True)
class ForwardCloudScope:
    """Selected cloud accounts; every cloud child row is closed over this set."""

    account_keys: frozenset[tuple[str, str]] = frozenset()

    @classmethod
    def from_account_rows(
        cls,
        rows: Iterable[Row],
        *,
        cloud_types: Sequence[str] = (),
        cloud_account_ids: Sequence[str] = (),
    ) -> ForwardCloudScope:
        allowed_types = {scope_token(v) for v in cloud_types if str(v).strip()}
        allowed_ids = {str(v).strip() for v in cloud_account_ids if str(v).strip()}
        selected: set[tuple[str, str]] = set()
        for row in rows:
            account_id = str(row.get("account_id") or "").strip()
            if not account_id:
                continue
            if allowed_ids and account_id not in allowed_ids:
                continue
            if allowed_types and scope_token(row.get("cloud_type")) not in allowed_types:
                continue
            selected.add(cloud_account_key(row))
        return cls(account_keys=frozenset(selected))

    def includes(self, row: Mapping[str, Any]) -> bool:
        return cloud_account_key(row) in self.account_keys


def _sorted_set(values: Iterable[Any]) -> list[str]:
    return sorted({str(v).strip() for v in values if str(v).strip()})


def build_scope_fingerprint(
    *,
    table_names: Iterable[str] = (),
    device_vendors: Iterable[str] = (),
    device_types: Iterable[str] = (),
    device_models: Iterable[str] = (),
    cloud_types: Iterable[str] = (),
    cloud_account_ids: Iterable[str] = (),
    query_commits: Mapping[str, str] | None = None,
    schema_mapping: Any = None,
) -> str:
    """A stable identity for a sync's ownership scope.

    A change in any input means rows from a previous run cannot be trusted as a
    baseline, so incremental diffs restart from a full load.
    """
    payload = {
        "cloud_account_ids": _sorted_set(cloud_account_ids),
        "cloud_types": _sorted_set(cloud_types),
        "device_models": _sorted_set(device_models),
        "device_types": _sorted_set(device_types),
        "device_vendors": _sorted_set(device_vendors),
        "query_commits": dict(sorted((query_commits or {}).items())),
        "schema_mapping": schema_mapping,
        "table_names": _sorted_set(table_names),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()

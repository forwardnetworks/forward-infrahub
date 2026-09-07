"""Jinja filters available in ``transforms:`` expressions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_VENDOR_OVERRIDES = {
    "f5": "F5",
    "hpe": "HPE",
    "hp": "HP",
    "vmware": "VMware",
    "aws": "AWS",
    "gcp": "GCP",
    "azure": "Azure",
    "oci": "OCI",
    "nsx": "NSX",
    "a10": "A10",
    "paloalto": "Palo Alto Networks",
    "palo_alto_networks": "Palo Alto Networks",
    "checkpoint": "Check Point",
    "check_point": "Check Point",
    "sonicwall": "SonicWall",
    "fortinet": "Fortinet",
    "cisco_meraki": "Cisco Meraki",
}


def enum_token(value: Any) -> str:
    """``Vendor.CISCO`` -> ``cisco``; leaves plain strings case-folded."""
    return str(value or "").strip().rsplit(".", 1)[-1].casefold()


def title_vendor(value: Any) -> str:
    """``Vendor.PALO_ALTO_NETWORKS`` -> ``Palo Alto Networks``."""
    token = enum_token(value)
    if not token:
        return ""
    if token in _VENDOR_OVERRIDES:
        return _VENDOR_OVERRIDES[token]
    return " ".join(part.capitalize() for part in token.split("_"))


def oper_status(enabled: Any) -> str:
    return "active" if bool(enabled) else "disabled"


def join_key(parts: Iterable[Any]) -> str:
    """Join identifier parts the way DiffSync builds unique ids."""
    return "__".join(str(part) for part in parts)


CUSTOM_FILTERS = {
    "enum_token": enum_token,
    "title_vendor": title_vendor,
    "oper_status": oper_status,
    "join_key": join_key,
}

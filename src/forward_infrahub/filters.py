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


#: The layer-two modes the destination accepts. Anything else is left unset:
#: a mode Forward reports and this does not name is better absent than
#: guessed, and the destination rejects a value outside its own list anyway.
L2_MODES = frozenset({"access", "trunk", "trunk_all"})


def interface_status(admin_up: Any, oper_up: Any = None) -> str:
    """An interface's status from the two states Forward reports separately.

    Administratively down is ``disabled``: somebody turned it off. Up but not
    carrying is ``free``, which the destination describes as unused — an
    admin-up port that is dark is exactly that, and calling it disabled would
    say a person had done something they had not.
    """
    if not bool(admin_up):
        return "disabled"
    return "active" if bool(oper_up) else "free"


def l2_mode(value: Any) -> str | None:
    """The layer-two mode, or nothing when Forward did not report a usable one."""
    text = str(value or "").strip().casefold()
    return text if text in L2_MODES else None


def join_key(parts: Iterable[Any]) -> str:
    """Join identifier parts the way DiffSync builds unique ids."""
    return "__".join(str(part) for part in parts)


CUSTOM_FILTERS = {
    "enum_token": enum_token,
    "title_vendor": title_vendor,
    "interface_status": interface_status,
    "l2_mode": l2_mode,
    "join_key": join_key,
}

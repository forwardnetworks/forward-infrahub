"""Allowlists are written by hand, so they are checked before they reach a query."""

from __future__ import annotations

import pytest
from forward_sdk.models import DeviceType, Vendor

from forward_infrahub.settings import AdapterSettings


@pytest.mark.parametrize("spelling", ["cisco", "CISCO", "Vendor.CISCO", "Vendor.cisco", " cisco "])
def test_every_spelling_of_a_vendor_resolves_to_its_member(spelling: str) -> None:
    """An operator should not have to know how Forward capitalises an enum."""
    assert AdapterSettings(device_vendors=[spelling]).device_vendors == ["CISCO"]


def test_member_names_come_from_the_api_description() -> None:
    """Not from a rule about capitalisation, which is what used to be assumed."""
    resolved = AdapterSettings(device_vendors=["palo_alto_networks", "a10"]).device_vendors
    assert set(resolved) <= {member.value for member in Vendor}
    assert resolved == ["PALO_ALTO_NETWORKS", "A10"]


def test_device_types_are_checked_against_their_own_enum() -> None:
    assert AdapterSettings(device_types=["switch"]).device_types == ["SWITCH"]
    assert "SWITCH" in {member.value for member in DeviceType}


def test_a_misspelled_vendor_is_refused_with_a_suggestion() -> None:
    """Left alone it would become a query that quietly matches nothing."""
    with pytest.raises(ValueError, match="Did you mean CISCO"):
        AdapterSettings(device_vendors=["cicso"])


def test_an_unknown_vendor_says_how_to_proceed() -> None:
    """A vendor Forward added since this SDK release is a real possibility."""
    with pytest.raises(ValueError, match="upgrade forward-sdk"):
        AdapterSettings(device_vendors=["Vendor.NOPE"])


def test_models_are_not_checked_because_they_are_free_text() -> None:
    assert AdapterSettings(device_models=["anything-at-all"]).device_models == ["anything-at-all"]


def test_blank_entries_are_dropped() -> None:
    assert AdapterSettings(device_vendors=["cisco", "  "]).device_vendors == ["CISCO"]


def test_an_empty_allowlist_means_every_device() -> None:
    settings = AdapterSettings()
    assert settings.device_vendors == []
    assert not settings.device_scope_active

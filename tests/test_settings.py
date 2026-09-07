"""Allowlists are written by hand, so they are checked before they reach a query."""

from __future__ import annotations

import re

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


def test_an_unknown_vendor_says_where_the_list_came_from() -> None:
    """A value with no member has two causes and the operator needs both.

    It is a typo, or Forward added the vendor after this list was recorded. The
    message names the page the list was read from, which answers either.
    """
    with pytest.raises(ValueError, match="type-vendor") as raised:
        AdapterSettings(device_vendors=["Vendor.NOPE"])
    assert "REST schema" in str(raised.value)


def test_models_are_not_checked_because_they_are_free_text() -> None:
    assert AdapterSettings(device_models=["anything-at-all"]).device_models == ["anything-at-all"]


def test_blank_entries_are_dropped() -> None:
    assert AdapterSettings(device_vendors=["cisco", "  "]).device_vendors == ["CISCO"]


def test_an_empty_allowlist_means_every_device() -> None:
    settings = AdapterSettings()
    assert settings.device_vendors == []
    assert not settings.device_scope_active


def test_a_vendor_nqe_names_but_the_sdk_does_not_is_accepted() -> None:
    """NQE and the REST schema disagree about some names, and NQE is what runs.

    The SDK generates its enums from the REST schema, where this vendor is GD.
    A predicate is built from these names, so using the REST spelling would emit
    a member NQE has no such thing as. Confirmed live by the SDK maintainers:
    Vendor.GENERAL_DYNAMICS is accepted, Vendor.GD fails as an unknown
    alternative.
    """
    assert AdapterSettings(device_vendors=["general_dynamics"]).device_vendors == [
        "GENERAL_DYNAMICS"
    ]
    assert AdapterSettings(device_vendors=["azure"]).device_vendors == ["AZURE"]


def test_validation_uses_the_nqe_namespace_not_the_rest_one() -> None:
    """NQE's DeviceType says what a device does and has 16 members; the REST one
    the SDK models has 75 and includes cloud constructs. Deriving one from the
    other is how the allowlist came to refuse valid values and accept invalid
    ones at the same time.
    """
    from forward_infrahub.nqe_types import DEVICE_TYPE, VENDOR, nqe_members

    assert nqe_members(DEVICE_TYPE) != {member.value for member in DeviceType}
    assert nqe_members(VENDOR) != {member.value for member in Vendor}
    assert len(nqe_members(DEVICE_TYPE)) < len({member.value for member in DeviceType})


def test_nqe_only_values_are_accepted() -> None:
    """Values NQE has and the SDK does not must still be usable."""
    assert AdapterSettings(device_types=["load_balancer"]).device_types == ["LOAD_BALANCER"]
    assert AdapterSettings(device_types=["internet"]).device_types == ["INTERNET"]


@pytest.mark.parametrize("rest_only", ["gd", "microsoft", "ibm"])
def test_a_vendor_only_the_rest_schema_names_is_refused(rest_only: str) -> None:
    """Accepting it would build a predicate NQE rejects at compile time."""
    with pytest.raises(ValueError, match="NQE Vendor members"):
        AdapterSettings(device_vendors=[rest_only])


@pytest.mark.parametrize("rest_only", ["aws_subnet", "azure_cloud"])
def test_a_device_type_only_the_rest_schema_names_is_refused(rest_only: str) -> None:
    with pytest.raises(ValueError, match="NQE DeviceType members"):
        AdapterSettings(device_types=[rest_only])


def test_the_error_points_at_the_page_the_list_came_from() -> None:
    with pytest.raises(ValueError, match=re.escape("docs.fwd.app")):
        AdapterSettings(device_vendors=["nope"])

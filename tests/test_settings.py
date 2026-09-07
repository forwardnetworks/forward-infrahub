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
    """A vendor Forward added since this SDK release is the other cause.

    The message names the SDK version its enum came from, so an operator can
    tell a typo from a list that has fallen behind their server.
    """
    from forward_sdk import __version__ as sdk_version

    with pytest.raises(ValueError, match="upgrade forward-sdk") as raised:
        AdapterSettings(device_vendors=["Vendor.NOPE"])
    assert sdk_version in str(raised.value)


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
    a member NQE has no such thing as.
    """
    assert AdapterSettings(device_vendors=["general_dynamics"]).device_vendors == [
        "GENERAL_DYNAMICS"
    ]
    assert AdapterSettings(device_vendors=["azure"]).device_vendors == ["AZURE"]


@pytest.mark.parametrize("rest_only", ["gd", "microsoft", "ibm"])
def test_a_vendor_only_the_rest_schema_names_is_refused(rest_only: str) -> None:
    """Accepting it would build a predicate NQE rejects at compile time."""
    with pytest.raises(ValueError, match="members this build knows"):
        AdapterSettings(device_vendors=[rest_only])


def test_the_two_namespaces_do_not_claim_the_same_name() -> None:
    """A name cannot be both NQE-only and REST-only; that would be a bad record."""
    from forward_infrahub.nqe_types import NQE_ONLY_MEMBERS, REST_ONLY_MEMBERS

    for enum_type, nqe_only in NQE_ONLY_MEMBERS.items():
        assert not (nqe_only & REST_ONLY_MEMBERS.get(enum_type, frozenset()))


def test_rest_only_names_really_are_in_the_sdk_enum() -> None:
    """So the correction shrinks if the SDK catches up, rather than going stale."""
    from forward_infrahub.nqe_types import REST_ONLY_MEMBERS

    assert REST_ONLY_MEMBERS["Vendor"] <= {member.value for member in Vendor}

"""The recorded NQE member lists, and the generated ones that will replace them."""

from __future__ import annotations

from forward_infrahub.nqe_types import (
    DEVICE_TYPE,
    KNOWN_PREFIXES,
    MEMBER_SOURCES,
    OS,
    TYPE_SOURCES,
    VENDOR,
    nqe_members,
)


def test_every_type_we_validate_has_a_page_for_the_operator() -> None:
    """A refused value needs somewhere to look, which the SDK cannot provide."""
    assert set(MEMBER_SOURCES) == {VENDOR, DEVICE_TYPE, OS}
    assert all(url.startswith("https://docs.fwd.app/") for url in MEMBER_SOURCES.values())


def test_every_type_name_has_a_prefix_and_a_provenance_line() -> None:
    assert {f"{name}." for name in TYPE_SOURCES} == set(KNOWN_PREFIXES)


def test_the_types_we_rely_on_are_in_the_data_model() -> None:
    """An empty result means the SDK does not describe the type at all.

    Validation then accepts whatever it is handed, which is right, but it is
    silent. These three are the ones an allowlist is checked against, so their
    absence would quietly turn the check off.
    """
    for enum_type in (VENDOR, DEVICE_TYPE, OS):
        assert nqe_members(enum_type), enum_type


def test_the_member_names_are_nqe_identifiers() -> None:
    for enum_type in (VENDOR, DEVICE_TYPE, OS):
        for name in nqe_members(enum_type):
            assert name.isupper(), f"{enum_type}.{name} is not upper case"
            assert name.replace("_", "").isalnum(), f"{enum_type}.{name} is not an identifier"


def test_the_namespaces_still_differ() -> None:
    """If these ever converge, the reason for all of this has gone away."""
    from forward_sdk.models import DeviceType, Vendor

    assert nqe_members(VENDOR) != {member.value for member in Vendor}
    assert nqe_members(DEVICE_TYPE) != {member.value for member in DeviceType}

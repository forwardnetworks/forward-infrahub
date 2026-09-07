"""The recorded NQE member lists, and the generated ones that will replace them."""

from __future__ import annotations

import pytest

from forward_infrahub.nqe_types import KNOWN_PREFIXES, MEMBER_SOURCES, NQE_MEMBERS, TYPE_SOURCES


def test_every_recorded_list_says_where_it_came_from() -> None:
    assert set(NQE_MEMBERS) <= set(MEMBER_SOURCES)
    assert all(url.startswith("https://docs.fwd.app/") for url in MEMBER_SOURCES.values())


def test_every_type_name_has_a_prefix_and_a_provenance_line() -> None:
    assert {f"{name}." for name in TYPE_SOURCES} == set(KNOWN_PREFIXES)


def test_recorded_members_look_like_nqe_member_names() -> None:
    for enum_type, names in NQE_MEMBERS.items():
        assert names, enum_type
        for name in names:
            assert name.isupper(), f"{enum_type}.{name} is not upper case"
            assert name.replace("_", "").isalnum(), f"{enum_type}.{name} is not an identifier"


def test_the_lists_match_the_generated_ones_when_the_sdk_has_them() -> None:
    """Cross-check against forward-sdk's generated NQE names, once it ships them.

    These lists were transcribed by hand from rendered documentation, and the
    SDK generates its own from the data model those pages are built from. Two
    routes to the same facts, so a disagreement means one of them is wrong.

    Skipped until the SDK carries them, because depending on an unreleased API
    would make this package uninstallable. When it releases, this test is what
    licences deleting NQE_MEMBERS rather than migrating it.
    """
    enums = pytest.importorskip(
        "forward_sdk.nqe.enums",
        reason="forward-sdk does not generate NQE member names yet",
    )
    for enum_type, recorded in NQE_MEMBERS.items():
        assert set(recorded) == set(enums.members(enum_type)), enum_type

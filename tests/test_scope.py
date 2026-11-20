"""Which devices and cloud accounts a sync claims to speak for."""

from __future__ import annotations

import pytest

from forward_infrahub.scope import (
    ForwardCloudScope,
    ForwardDeviceScope,
    build_scope_fingerprint,
    scope_token,
)

DEVICES = [
    {"name": "sw1", "vendor": "Vendor.CISCO", "device_type": "DeviceType.SWITCH", "model": "C9300"},
    {
        "name": "rtr1",
        "vendor": "Vendor.JUNIPER",
        "device_type": "DeviceType.ROUTER",
        "model": "MX204",
    },
    {
        "name": "fw1",
        "vendor": "Vendor.PALO_ALTO_NETWORKS",
        "device_type": "DeviceType.FIREWALL",
        "model": "PA-3220",
    },
]


def test_scope_token_strips_the_enum_prefix() -> None:
    assert scope_token("Vendor.CISCO") == "cisco"
    assert scope_token("cisco") == "cisco"
    assert scope_token(None) == ""


def test_no_allowlist_selects_everything() -> None:
    scope = ForwardDeviceScope.from_device_rows(DEVICES)
    assert scope.device_names == {"sw1", "rtr1", "fw1"}


def test_allowlists_within_a_field_are_alternatives() -> None:
    scope = ForwardDeviceScope.from_device_rows(DEVICES, vendors=["cisco", "juniper"])
    assert scope.device_names == {"sw1", "rtr1"}


def test_allowlists_across_fields_must_all_match() -> None:
    scope = ForwardDeviceScope.from_device_rows(
        DEVICES, vendors=["cisco"], types=["DeviceType.ROUTER"]
    )
    assert scope.device_names == set()


def test_membership_by_relation_column() -> None:
    scope = ForwardDeviceScope.from_device_rows(DEVICES, vendors=["cisco"])
    assert scope.includes({"name": "sw1"}, column="name")
    assert not scope.includes({"name": "rtr1"}, column="name")
    assert scope.includes({"device": "sw1"}, column="device")
    assert scope.includes({"scope_devices": ["rtr1", "sw1"]}, column="scope_devices")
    assert not scope.includes({"scope_devices": ["rtr1"]}, column="scope_devices")


def test_a_row_with_no_device_relation_fails_closed() -> None:
    """Route tables carry no device column; a filtered run must not claim them."""
    scope = ForwardDeviceScope.from_device_rows(DEVICES, vendors=["cisco"])
    assert not scope.includes({"prefix": "10.0.0.0/24"}, column=None)


def test_cloud_scope_is_provider_qualified() -> None:
    rows = [
        {"cloud_type": "aws", "account_id": "1"},
        {"cloud_type": "azure", "account_id": "1"},
    ]
    scope = ForwardCloudScope.from_account_rows(rows, cloud_types=["aws"])
    assert scope.includes(rows[0])
    assert not scope.includes(rows[1])


def test_fingerprint_changes_with_every_input() -> None:
    first = build_scope_fingerprint(table_names=["devices"], device_vendors=["cisco"])
    assert first == build_scope_fingerprint(table_names=["devices"], device_vendors=["cisco"])
    assert first != build_scope_fingerprint(table_names=["devices"], device_vendors=["juniper"])
    assert first != build_scope_fingerprint(
        table_names=["devices"], device_vendors=["cisco"], cloud_types=["aws"]
    )
    assert first != build_scope_fingerprint(
        table_names=["devices"],
        device_vendors=["cisco"],
        query_commits={"forward_devices": "abc"},
    )


def test_fingerprint_ignores_order_and_blanks() -> None:
    assert build_scope_fingerprint(device_vendors=["a", "b"]) == build_scope_fingerprint(
        device_vendors=["b", "a", " "]
    )


def test_a_cache_written_by_an_older_format_is_not_a_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows cached before transforms stopped editing them in place hold transformed values.

    Bumping the cache format changes the fingerprint, so such a cache is refused and
    the next run reads in full instead of folding a diff into rows that are wrong.
    """
    from forward_infrahub import scope

    current = build_scope_fingerprint(table_names=["devices"])
    monkeypatch.setattr(scope, "CACHE_FORMAT", scope.CACHE_FORMAT - 1)

    assert build_scope_fingerprint(table_names=["devices"]) != current

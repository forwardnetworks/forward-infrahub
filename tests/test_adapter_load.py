"""Loading the fixture through the adapter, end to end."""

from __future__ import annotations

from typing import Any

import pytest
from infrahub_sync import SyncConfig

from forward_infrahub.adapter import ForwardnetworksAdapter

from .conftest import build_models


def uids(adapter: Any, kind: str) -> set[str]:
    return {obj.get_unique_id() for obj in adapter.get_all(kind)}


def one(adapter: Any, kind: str, uid: str) -> Any:
    return adapter.get(kind, uid)


def test_every_kind_loads(adapter: Any) -> None:
    assert uids(adapter, "OrganizationManufacturer") == {"Arista"}
    assert uids(adapter, "DcimPlatform") == {"ARISTA_EOS"}
    assert uids(adapter, "DcimDeviceType") == {"MODEL-A"}
    assert uids(adapter, "DcimDevice") == {"device-001", "device-002"}
    assert uids(adapter, "IpamVRF") == {"BLUE"}
    assert uids(adapter, "CloudAccount") == {"aws__111122223333"}


def test_vendor_transform_titles_the_enum(adapter: Any) -> None:
    """Vendor.ARISTA is a Forward enum; Infrahub should show a readable name."""
    assert one(adapter, "OrganizationManufacturer", "Arista").name == "Arista"
    assert one(adapter, "DcimPlatform", "ARISTA_EOS").manufacturer == "Arista"


def test_location_variants_collapse_to_one_site(adapter: Any) -> None:
    """'30TH ST' and '30TH STREET' are the same site, and devices must not scatter."""
    assert uids(adapter, "LocationSite") == {"500 W 30TH ST"}
    assert one(adapter, "DcimDevice", "device-002").location == "500 W 30TH ST"


def test_device_carries_serial_and_references(adapter: Any) -> None:
    device = one(adapter, "DcimDevice", "device-001")
    assert device.serial_number == "SERIAL-001"
    assert device.device_type == "MODEL-A"
    assert device.platform == "ARISTA_EOS"
    assert device.status == "active"


def test_interfaces_split_by_kind(adapter: Any) -> None:
    assert uids(adapter, "InterfacePhysical") == {
        "device-001__Ethernet1",
        "device-002__Ethernet1",
    }
    assert uids(adapter, "InterfaceVirtual") == {"device-001__Loopback0"}


def test_interface_status_follows_operational_state(adapter: Any) -> None:
    assert one(adapter, "InterfacePhysical", "device-001__Ethernet1").status == "active"
    assert one(adapter, "InterfacePhysical", "device-002__Ethernet1").status == "disabled"


def test_interface_on_an_unknown_device_is_skipped(adapter: Any) -> None:
    """A row referencing a device this sync never loaded must not be written."""
    assert "device-999__Ethernet9" not in uids(adapter, "InterfacePhysical")
    assert any("InterfacePhysical:unresolved device" in key for key in adapter.skipped)


def test_namespaces_include_the_default(adapter: Any) -> None:
    assert uids(adapter, "IpamNamespace") == {"BLUE", "default"}


def test_prefixes_come_from_both_families(adapter: Any) -> None:
    assert uids(adapter, "IpamPrefix") == {
        "10.0.0.0/24__BLUE",
        "192.168.0.0/24__default",
        "2001:db8:1::/64__default",
    }


def test_prefix_vrf_is_unset_in_the_default_namespace(adapter: Any) -> None:
    assert one(adapter, "IpamPrefix", "10.0.0.0/24__BLUE").vrf == "BLUE"
    assert one(adapter, "IpamPrefix", "192.168.0.0/24__default").vrf is None


def test_ip_address_resolves_a_generic_interface_reference(adapter: Any) -> None:
    """`interface` points at InterfaceLayer3, which is generic over both kinds."""
    loopback = one(adapter, "IpamIPAddress", "192.168.0.1/24__default")
    assert loopback.interface == "device-001__Loopback0"
    ethernet = one(adapter, "IpamIPAddress", "10.0.0.1/24__BLUE")
    assert ethernet.interface == "device-001__Ethernet1"


def test_primary_address_is_set_from_the_address_side(adapter: Any) -> None:
    """Avoids a device -> address -> interface -> device load cycle."""
    assert one(adapter, "IpamIPAddress", "192.168.0.1/24__default").primary_of == ["device-001"]
    assert one(adapter, "IpamIPAddress", "10.0.0.1/24__BLUE").primary_of == []


def test_cable_lists_both_endpoints(adapter: Any) -> None:
    cable = one(adapter, "DcimCable", "device-001:Ethernet1 <> device-002:Ethernet1")
    assert cable.connected_endpoints == ["device-001__Ethernet1", "device-002__Ethernet1"]
    assert cable.status == "connected"


def test_cloud_hierarchy_uses_provider_ids(adapter: Any) -> None:
    """Names change; ids do not, so identity must never depend on a name."""
    assert uids(adapter, "CloudVPC") == {"aws__111122223333__vpc-abc"}
    assert uids(adapter, "CloudSubnet") == {"aws__111122223333__vpc-abc__subnet-abc"}
    assert one(adapter, "CloudVPC", "aws__111122223333__vpc-abc").cidrs == ["10.1.0.0/16"]


def test_one_execution_per_query(adapter: Any) -> None:
    """Kinds sharing a table share its fetch: interfaces feed two kinds, cloud networks two."""
    calls = adapter.client.calls
    assert len(calls) == len(set(calls)), calls
    assert calls.count("forward_interfaces") == 1
    assert calls.count("forward_cloud_networks") == 1


def test_local_id_comes_from_the_query_primary_key(adapter: Any) -> None:
    assert one(adapter, "DcimDevice", "device-001").local_id == "device-001"
    assert (
        one(adapter, "InterfacePhysical", "device-001__Ethernet1").local_id
        == "device-001|Ethernet1"
    )


def test_snapshot_is_resolved_once_and_pinned(adapter: Any) -> None:
    assert adapter.snapshot is not None
    assert adapter.snapshot.id == "snap-2"
    assert adapter.snapshot.complete


def test_scope_fingerprint_is_stable_and_set(adapter: Any, replay_config: SyncConfig) -> None:
    second = ForwardnetworksAdapter(
        target="infrahub", adapter=replay_config.source, config=replay_config
    )
    for name, model in build_models(replay_config).items():
        setattr(second, name, model)
    second.top_level = list(replay_config.order)  # type: ignore[misc]
    second.load()
    assert adapter.scope_fingerprint == second.scope_fingerprint
    assert len(adapter.scope_fingerprint) == 64


@pytest.mark.parametrize(
    ("vendors", "expected"),
    [
        (["cisco"], set()),
        (["Vendor.ARISTA"], {"device-001", "device-002"}),
        (["arista"], {"device-001", "device-002"}),
    ],
)
def test_device_scope_accepts_enum_or_token(
    replay_config: SyncConfig, vendors: list[str], expected: set[str]
) -> None:
    settings = dict(replay_config.source.settings or {})
    settings["device_vendors"] = vendors
    replay_config.source.settings = settings
    instance = ForwardnetworksAdapter(
        target="infrahub", adapter=replay_config.source, config=replay_config
    )
    for name, model in build_models(replay_config).items():
        setattr(instance, name, model)
    instance.top_level = list(replay_config.order)  # type: ignore[misc]
    instance.load()
    assert uids(instance, "DcimDevice") == expected

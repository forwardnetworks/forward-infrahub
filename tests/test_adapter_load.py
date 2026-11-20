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
    """'ELM ST' and 'ELM STREET' are the same site, and devices must not scatter."""
    assert uids(adapter, "LocationSite") == {"120 W ELM ST"}
    assert one(adapter, "DcimDevice", "device-002").location == "120 W ELM ST"


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
        "device-002__Ethernet2",
    }
    assert uids(adapter, "InterfaceVirtual") == {"device-001__Loopback0"}
    assert uids(adapter, "InterfaceLag") == {"device-001__Port-Channel1"}


def test_interface_status_distinguishes_dark_from_switched_off(adapter: Any) -> None:
    """Admin-down is somebody's decision; admin-up and dark is an unused port."""
    assert one(adapter, "InterfacePhysical", "device-001__Ethernet1").status == "active"
    assert one(adapter, "InterfacePhysical", "device-002__Ethernet1").status == "free"
    assert one(adapter, "InterfacePhysical", "device-002__Ethernet2").status == "disabled"


def test_a_member_names_its_lag(adapter: Any) -> None:
    lag = one(adapter, "InterfaceLag", "device-001__Port-Channel1")
    assert lag.bundle_number == 1
    assert one(adapter, "InterfacePhysical", "device-001__Ethernet1").bundle == (
        "device-001__Port-Channel1"
    )


def test_vlan_membership_survives_a_collapsed_site_name(adapter: Any) -> None:
    """The access port names its site the other way; the key must still resolve.

    The VLAN is written under one site name and the port reports another
    spelling of it, so this passes only if the reference was built after the
    names were collapsed, not in the query.
    """
    port = one(adapter, "InterfacePhysical", "device-002__Ethernet1")
    assert port.l2_mode == "access"
    assert port.untagged_vlan == "VLAN 100 @ 120 W ELM ST"
    assert uids(adapter, "IpamVLAN") == {"VLAN 100 @ 120 W ELM ST"}
    assert one(adapter, "IpamVLAN", "VLAN 100 @ 120 W ELM ST").description == "Users"


def test_a_trunk_carries_the_vlans_its_device_reports(adapter: Any) -> None:
    lag = one(adapter, "InterfaceLag", "device-001__Port-Channel1")
    assert lag.l2_mode == "trunk"
    assert lag.tagged_vlan == ["VLAN 100 @ 120 W ELM ST"]


def test_an_interface_with_no_layer_two_mode_is_left_unset(adapter: Any) -> None:
    """Absent is not access: a mode Forward did not report must not be invented."""
    assert one(adapter, "InterfacePhysical", "device-001__Ethernet1").l2_mode is None


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


def test_device_tags_become_nodes_and_relate_back(adapter: Any) -> None:
    """Tags come from the device rows, so they cost no second query."""
    assert uids(adapter, "BuiltinTag") == {"core", "managed", "edge"}
    assert sorted(one(adapter, "DcimDevice", "device-001").tags) == ["core", "managed"]
    assert one(adapter, "DcimDevice", "device-002").tags == ["edge"]


def test_a_vrf_carries_only_what_forward_knows(adapter: Any) -> None:
    """A network instance has no route distinguisher, so none is written.

    The mapping used to send an empty string for one on every run, which is
    not the same as leaving a field Forward cannot answer for unset.
    """
    vrf = one(adapter, "IpamVRF", "BLUE")
    assert vrf.name == "BLUE"
    assert not hasattr(vrf, "vrf_rd") or vrf.vrf_rd is None


def test_a_many_relationship_is_resolved_in_the_order_infrahub_reads_it_back(
    adapter: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """infrahub-sync sorts a many-relationship as plain strings when it reads it.

    A list that differs only in order is a change to the diff, so every ordering
    Forward can return must come out the same: a trunk's VLANs, a device's tags,
    and a cable's two ends (``sw1`` sorts after ``sw10`` once the interface is
    joined on, because ``0`` sorts before ``_``).
    """
    monkeypatch.setattr(adapter, "_peer_exists", lambda kind, identifier: True)
    ends = ["sw1__Ethernet1", "sw10__Ethernet1"]

    forward = adapter._resolve_reference("InterfacePhysical", ends)
    reverse = adapter._resolve_reference("InterfacePhysical", list(reversed(ends)))

    assert forward == reverse == sorted(ends) == ["sw10__Ethernet1", "sw1__Ethernet1"]


def test_a_software_release_carries_its_dates_its_platform_and_its_devices(adapter: Any) -> None:
    assert uids(adapter, "DcimSoftwareVersion") == {"ARISTA_EOS 1.2.3", "ARISTA_EOS 1.2.4"}
    dated = one(adapter, "DcimSoftwareVersion", "ARISTA_EOS 1.2.3")
    assert dated.platform == "ARISTA_EOS"
    assert dated.version == "1.2.3"
    assert dated.end_of_support == "2027-06-30T12:00:00Z"
    assert dated.end_of_maintenance == "2025-06-30T12:00:00Z"


def test_a_release_with_no_support_dates_leaves_them_unset(adapter: Any) -> None:
    """Forward has no lifecycle data for some vendors: unset, not an empty string."""
    undated = one(adapter, "DcimSoftwareVersion", "ARISTA_EOS 1.2.4")
    assert undated.end_of_support is None
    assert undated.end_of_maintenance is None
    assert undated.announcement_url is None


def test_a_vulnerability_carries_its_severity_date_and_devices(adapter: Any) -> None:
    assert uids(adapter, "SecurityVulnerability") == {
        "CVE-2026-0001",
        "CVE-2026-0002",
        "CVE-2025-0003",
    }
    critical = one(adapter, "SecurityVulnerability", "CVE-2026-0001")
    assert critical.severity == "critical"
    assert critical.published == "2026-01-15T12:00:00Z"
    assert critical.cisa_kev is True
    assert critical.devices == ["device-001", "device-002"]
    assert one(adapter, "SecurityVulnerability", "CVE-2026-0002").cisa_kev is False


def test_a_device_that_was_not_loaded_is_left_out_of_a_vulnerabilitys_devices(
    adapter: Any,
) -> None:
    """device-999 is in the fixture's vulnerability row but is not a loaded device.

    The CVE keeps its row and links the devices that did load, instead of
    disappearing because one of them did not.
    """
    medium = one(adapter, "SecurityVulnerability", "CVE-2025-0003")
    assert medium.devices == ["device-002"]
    assert adapter.left_out["SecurityVulnerability.devices"] == 1


def test_a_scoped_sync_lists_only_the_devices_it_owns(adapter: Any) -> None:
    """A row kept because one device is in scope must not name the others."""
    from forward_infrahub.scope import ForwardDeviceScope
    from forward_infrahub.tables import parse_mapping

    adapter.device_scope = ForwardDeviceScope(device_names=frozenset({"device-001"}))
    rows = [
        {"cve_id": "A", "scope_devices": ["device-001", "device-002"]},
        {"cve_id": "B", "scope_devices": ["device-002"]},
    ]

    kept = adapter.scoped(parse_mapping("nqe:vulnerabilities"), rows)

    assert kept == [{"cve_id": "A", "scope_devices": ["device-001"]}]


def test_a_list_of_devices_none_of_which_loaded_gives_the_row_nothing_to_link(
    adapter: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forward_infrahub.adapter import MISSING

    monkeypatch.setattr(adapter, "_peer_exists", lambda kind, identifier: False)

    resolved = adapter._resolve_reference("DcimDevice", ["ghost-1", "ghost-2"], partial=True)

    assert resolved is MISSING


def test_a_calendar_date_shows_as_the_same_date_in_any_timezone_infrahub_is_viewed_in(
    adapter: Any,
) -> None:
    """Infrahub renders a DateTime in the viewer's timezone, and these are dates.

    Midnight UTC reads as the previous day for anyone west of Greenwich, which is
    how a published CVE showed up a day early. The instant has to land on the same
    calendar date from UTC-11 to UTC+11.
    """
    from datetime import datetime, timedelta, timezone

    stored = one(adapter, "DcimSoftwareVersion", "ARISTA_EOS 1.2.3").end_of_support
    instant = datetime.fromisoformat(stored.replace("Z", "+00:00"))

    for hours in (-11, -5, 0, 5, 11):
        shown = instant.astimezone(timezone(timedelta(hours=hours)))
        assert shown.date().isoformat() == "2027-06-30", f"UTC{hours:+d} shows {shown}"


def _loaded_with_vulnerabilities_emptied(
    replay_config: SyncConfig,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    minimum: int,
    *,
    empty: bool = True,
) -> None:
    import json
    from pathlib import Path

    from forward_infrahub import adapter as adapter_module
    from forward_infrahub.adapter import ForwardnetworksAdapter

    from .conftest import FIXTURE

    payload = json.loads(Path(FIXTURE).read_text())
    if empty:
        payload["forward_vulnerabilities"] = []
    target = tmp_path / "no-vulnerabilities.json"
    target.write_text(json.dumps(payload))
    settings = dict(replay_config.source.settings or {})
    settings["replay_fixture"] = str(target)
    replay_config.source.settings = settings
    monkeypatch.setattr(adapter_module, "VULNERABILITY_WARNING_MIN_DEVICES", minimum)

    instance = ForwardnetworksAdapter(
        target="infrahub", adapter=replay_config.source, config=replay_config
    )
    for name, model in build_models(replay_config).items():
        setattr(instance, name, model)
    instance.top_level = list(replay_config.order)  # type: ignore[misc]
    instance.load()


def test_no_confirmed_vulnerabilities_across_a_network_warns_that_they_may_be_hidden(
    replay_config: SyncConfig,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Forward empties CVE data for a tier without it, with no error, so say so."""
    with caplog.at_level("WARNING"):
        _loaded_with_vulnerabilities_emptied(replay_config, tmp_path, monkeypatch, minimum=2)

    assert any("no confirmed vulnerabilities across 2 devices" in m for m in caplog.messages)


def test_a_small_network_with_no_vulnerabilities_does_not_warn(
    replay_config: SyncConfig,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        _loaded_with_vulnerabilities_emptied(replay_config, tmp_path, monkeypatch, minimum=3)

    assert not any("no confirmed vulnerabilities" in m for m in caplog.messages)


def test_vulnerabilities_present_means_no_warning(
    replay_config: SyncConfig,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ordinary fixture has CVEs. The load happens inside the test, so the log is real."""
    with caplog.at_level("WARNING"):
        _loaded_with_vulnerabilities_emptied(
            replay_config, tmp_path, monkeypatch, minimum=1, empty=False
        )

    assert not any("no confirmed vulnerabilities" in m for m in caplog.messages)


def test_a_device_names_the_release_it_runs(adapter: Any) -> None:
    """The link is written from the device, which has exactly one release."""
    assert one(adapter, "DcimDevice", "device-001").software_version == "ARISTA_EOS 1.2.3"
    assert one(adapter, "DcimDevice", "device-002").software_version == "ARISTA_EOS 1.2.4"


def test_a_device_with_no_reported_os_version_has_no_release_to_link() -> None:
    from pathlib import Path

    import yaml
    from infrahub_sync import DiffSyncModelMixin

    config = yaml.safe_load(
        (
            Path(__file__).parent.parent / "examples/forwardnetworks_to_infrahub/config.yml"
        ).read_text()
    )
    device = next(m for m in config["schema_mapping"] if m["name"] == "DcimDevice")
    expression = next(
        t["expression"] for t in device["transforms"] if t["field"] == "software_version"
    )
    known = {"platform": "IOS_XE", "os_version": "17.03.05"}
    unknown = {"platform": "IOS_XE", "os_version": ""}

    DiffSyncModelMixin.apply_transform(known, expression, "software_version")
    DiffSyncModelMixin.apply_transform(unknown, expression, "software_version")

    assert known["software_version"] == "IOS_XE 17.03.05"
    assert "software_version" not in unknown


def test_the_empty_cve_warning_counts_the_devices_a_scoped_sync_owns(
    adapter: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A filtered sync whose own devices have no CVEs must not warn for the whole network."""
    from forward_infrahub import adapter as adapter_module
    from forward_infrahub.scope import ForwardDeviceScope

    monkeypatch.setattr(adapter_module, "VULNERABILITY_WARNING_MIN_DEVICES", 2)

    adapter.device_scope = ForwardDeviceScope(device_names=frozenset({"device-001"}))
    with caplog.at_level("WARNING"):
        adapter._warn_if_vulnerabilities_may_be_hidden()
    assert not any("no confirmed vulnerabilities" in m for m in caplog.messages)

    adapter.device_scope = ForwardDeviceScope(device_names=frozenset({"device-001", "device-002"}))
    with caplog.at_level("WARNING"):
        adapter._warn_if_vulnerabilities_may_be_hidden()
    assert any("no confirmed vulnerabilities across 2 devices" in m for m in caplog.messages)


def _adapter_for(config: SyncConfig) -> Any:
    from forward_infrahub.adapter import ForwardnetworksAdapter

    instance = ForwardnetworksAdapter(target="infrahub", adapter=config.source, config=config)
    for name, model in build_models(config).items():
        setattr(instance, name, model)
    instance.top_level = list(config.order)  # type: ignore[misc]
    return instance


def test_taking_out_the_release_mapping_but_not_the_devices_link_to_it_stops_the_run(
    replay_config: SyncConfig,
) -> None:
    """Otherwise every device with an OS version is skipped, and everything hanging off one."""
    from forward_sdk import ForwardConfigurationError

    replay_config.schema_mapping = [
        e for e in replay_config.schema_mapping if e.name != "DcimSoftwareVersion"
    ]
    replay_config.order = [k for k in replay_config.order if k != "DcimSoftwareVersion"]

    with pytest.raises(ForwardConfigurationError, match=r"DcimDevice\.software_version"):
        _adapter_for(replay_config).load()


def test_opting_out_of_releases_and_cves_as_documented_still_loads_every_device(
    replay_config: SyncConfig,
) -> None:
    """The edits the example README and config.yml describe, and nothing more.

    The release mapping and its `order` entry, the device's `software_version` field
    and its transform, and the CVE mapping and its `order` entry.
    """
    gone = {"DcimSoftwareVersion", "SecurityVulnerability"}
    replay_config.schema_mapping = [e for e in replay_config.schema_mapping if e.name not in gone]
    replay_config.order = [k for k in replay_config.order if k not in gone]
    for element in replay_config.schema_mapping:
        if element.name == "DcimDevice":
            element.fields = [f for f in element.fields if f.name != "software_version"]
            element.transforms = [
                t for t in (element.transforms or []) if t.field != "software_version"
            ]

    instance = _adapter_for(replay_config)
    instance.load()

    assert uids(instance, "DcimDevice") == {"device-001", "device-002"}
    assert uids(instance, "InterfacePhysical")


def test_taking_the_release_out_of_order_but_leaving_its_mapping_also_stops_the_run(
    replay_config: SyncConfig,
) -> None:
    """A mapping outside `order` is not loaded, so the device's link to it is just as broken."""
    from forward_sdk import ForwardConfigurationError

    replay_config.order = [k for k in replay_config.order if k != "DcimSoftwareVersion"]

    with pytest.raises(ForwardConfigurationError, match=r"DcimDevice\.software_version"):
        _adapter_for(replay_config).load()


def test_a_field_that_resolves_no_reference_at_run_time_is_not_checked(
    replay_config: SyncConfig,
) -> None:
    """Loading resolves a reference only for a field with a mapping and no static value.

    A static value, or a field that maps nothing, never reaches reference resolution,
    so a reference on it must not be mistaken for a missing mapping.
    """
    from infrahub_sync import SchemaMappingField

    for element in replay_config.schema_mapping:
        if element.name == "DcimCable":
            element.fields.append(
                SchemaMappingField(
                    name="static_note", static="x", mapping="label", reference="KindNobodyLoads"
                )
            )
            element.fields.append(SchemaMappingField(name="bare", reference="KindNobodyLoads"))

    _adapter_for(replay_config).load()  # no ForwardConfigurationError

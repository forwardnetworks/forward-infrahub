"""Choosing a snapshot, and refusing to trust an incomplete one."""

from __future__ import annotations

from typing import Any

import pytest
from forward_sdk import ForwardError

from forward_infrahub.exceptions import InvalidConfiguration
from forward_infrahub.forward_client import ForwardSdkAdapter
from forward_infrahub.settings import AdapterSettings
from forward_infrahub.snapshot import (
    collected_probe_scope,
    completeness_from_metrics,
    resolve_snapshot,
)

CLEAN = {"numCollectionFailureDevices": 0, "numProcessingFailureDevices": 0}
FAILING = {"numCollectionFailureDevices": 3, "numProcessingFailureDevices": 0}


class FakeSnapshots:
    def __init__(self, metrics: dict[str, Any] | None = None, *, raises: bool = False) -> None:
        self._metrics = CLEAN if metrics is None else metrics
        self._raises = raises
        self.calls: list[str] = []

    def latest_processed_id(self, network_id: str) -> str | None:
        self.calls.append("latest_processed")
        return "snap-processed"

    def latest_collected_id(self, network_id: str, **_: Any) -> str:
        self.calls.append("latest_collected")
        return "snap-collected"

    def metrics(self, snapshot_id: str) -> dict[str, Any]:
        if self._raises:
            raise ForwardError("no metrics for you")
        return self._metrics


class FakeClient:
    def __init__(self, snapshots: FakeSnapshots) -> None:
        self.snapshots = snapshots


def resolve(selector: str, snapshots: FakeSnapshots) -> Any:
    settings = AdapterSettings(snapshot_id=selector)
    return resolve_snapshot(ForwardSdkAdapter(FakeClient(snapshots)), settings, "net-1")


def test_completeness_reads_every_failure_counter() -> None:
    assert completeness_from_metrics(CLEAN).complete
    incomplete = completeness_from_metrics(FAILING)
    assert not incomplete.complete
    assert incomplete.failure_count == 3


def test_missing_metrics_are_not_treated_as_success() -> None:
    unavailable = completeness_from_metrics(None, available=False)
    assert unavailable.status == "unavailable"
    assert not unavailable.complete


def test_non_numeric_counters_do_not_crash() -> None:
    assert completeness_from_metrics({"numCollectionFailureDevices": None}).complete


@pytest.mark.parametrize(
    ("selector", "expected", "call"),
    [
        ("latestProcessed", "snap-processed", "latest_processed"),
        ("latestCollected", "snap-collected", "latest_collected"),
    ],
)
def test_selectors_resolve_to_one_id(selector: str, expected: str, call: str) -> None:
    snapshots = FakeSnapshots()
    resolved = resolve(selector, snapshots)
    assert resolved.id == expected
    assert call in snapshots.calls


def test_an_explicit_id_is_used_as_given() -> None:
    snapshots = FakeSnapshots()
    assert resolve("snap-1234", snapshots).id == "snap-1234"
    assert "latest_processed" not in snapshots.calls


def test_a_network_with_no_processed_snapshot_is_an_error() -> None:
    snapshots = FakeSnapshots()
    snapshots.latest_processed_id = lambda network_id: None  # type: ignore[method-assign]
    with pytest.raises(InvalidConfiguration, match="no processed snapshot"):
        resolve("latestProcessed", snapshots)


def test_unavailable_metrics_leave_the_snapshot_unverified() -> None:
    resolved = resolve("snap-1", FakeSnapshots(raises=True))
    assert resolved.completeness.status == "unavailable"


def test_an_unfiltered_probe_asks_nothing_extra() -> None:
    assert collected_probe_scope(AdapterSettings()) == ""


def test_an_enum_field_is_compared_to_a_named_constant() -> None:
    """NQE rejects an enum compared against a string, whatever its spelling."""
    scope = collected_probe_scope(AdapterSettings(device_vendors=["Vendor.CISCO", "juniper"]))
    assert (
        "where (device.platform.vendor == Vendor.CISCO"
        " || device.platform.vendor == Vendor.JUNIPER)" in scope
    )
    assert '"cisco"' not in scope


def test_a_string_field_uses_the_list_form() -> None:
    scope = collected_probe_scope(AdapterSettings(device_models=["C9300"]))
    assert 'where toLowerCase(device.platform.model) in ["c9300"]' in scope


def test_every_spelling_of_a_vendor_reaches_one_clause() -> None:
    """The scope compares case-folded tokens, so the probe must accept the same."""
    expected = "where device.platform.vendor == Vendor.CISCO"
    for spelling in ("cisco", "CISCO", "Vendor.CISCO", "Vendor.cisco"):
        assert collected_probe_scope(AdapterSettings(device_vendors=[spelling])) == expected


def test_an_underscored_member_survives_the_round_trip() -> None:
    scope = collected_probe_scope(AdapterSettings(device_vendors=["palo_alto_networks"]))
    assert scope == "where device.platform.vendor == Vendor.PALO_ALTO_NETWORKS"


def test_probe_string_values_are_quoted_and_escaped() -> None:
    scope = collected_probe_scope(AdapterSettings(device_models=['odd"name']))
    assert '"odd\\"name"' in scope


def test_the_probe_scopes_the_latest_collected_lookup() -> None:
    """latestCollected must ask about this sync's devices, not the whole network."""
    captured: dict[str, Any] = {}

    class Recording(FakeSnapshots):
        def latest_collected_id(self, network_id: str, **kwargs: Any) -> str:
            captured.update(kwargs)
            return "snap-collected"

    settings = AdapterSettings(snapshot_id="latestCollected", device_vendors=["cisco"])
    resolve_snapshot(ForwardSdkAdapter(FakeClient(Recording())), settings, "net-1")
    assert "Vendor.CISCO" in captured["where"]

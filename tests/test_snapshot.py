"""Choosing a snapshot, and refusing to trust an incomplete one."""

from __future__ import annotations

from typing import Any

import pytest
from forward_sdk import ForwardConfigurationError, ForwardError

from forward_infrahub.settings import AdapterSettings
from forward_infrahub.snapshot import completeness_from_metrics, resolve_snapshot

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
    return resolve_snapshot(FakeClient(snapshots), settings, "net-1")


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
    with pytest.raises(ForwardConfigurationError, match="no processed snapshot"):
        resolve("latestProcessed", snapshots)


def test_unavailable_metrics_leave_the_snapshot_unverified() -> None:
    resolved = resolve("snap-1", FakeSnapshots(raises=True))
    assert resolved.completeness.status == "unavailable"

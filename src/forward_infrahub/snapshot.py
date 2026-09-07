"""Snapshot selection and the completeness gate."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from forward_sdk import ForwardConfigurationError, ForwardError

from forward_infrahub.settings import LATEST_COLLECTED, LATEST_PROCESSED, AdapterSettings

logger = logging.getLogger(__name__)

FAILURE_FIELDS = (
    "numCollectionFailureDevices",
    "numProcessingFailureDevices",
    "numCollectionFailureEndpoints",
    "numProcessingFailureEndpoints",
)


@dataclass(frozen=True, slots=True)
class SnapshotCompleteness:
    """What the snapshot metrics say about collection and processing failures."""

    status: str  # complete | incomplete | unavailable
    failure_counts: dict[str, int] = field(default_factory=dict)
    reason: str = ""

    @property
    def failure_count(self) -> int:
        return sum(self.failure_counts.values())

    @property
    def complete(self) -> bool:
        return self.status == "complete"


def completeness_from_metrics(
    metrics: Mapping[str, Any] | None, *, available: bool = True
) -> SnapshotCompleteness:
    counts: dict[str, int] = {}
    for name in FAILURE_FIELDS:
        raw = (metrics or {}).get(name, 0)
        try:
            counts[name] = max(0, int(raw or 0))
        except (TypeError, ValueError):
            counts[name] = 0
    if not available:
        return SnapshotCompleteness("unavailable", counts, "snapshot metrics unavailable")
    if sum(counts.values()):
        return SnapshotCompleteness(
            "incomplete", counts, "snapshot metrics report collection or processing failures"
        )
    return SnapshotCompleteness("complete", counts)


@dataclass(frozen=True, slots=True)
class ResolvedSnapshot:
    id: str
    selector: str
    completeness: SnapshotCompleteness

    @property
    def complete(self) -> bool:
        return self.completeness.complete


def resolve_snapshot(client: Any, settings: AdapterSettings, network_id: str) -> ResolvedSnapshot:
    """Turn the configured selector into one pinned snapshot id plus its completeness."""
    selector = (settings.snapshot_id or LATEST_PROCESSED).strip() or LATEST_PROCESSED
    if selector == LATEST_PROCESSED:
        snapshot_id = client.snapshots.latest_processed_id(network_id)
        if not snapshot_id:
            raise ForwardConfigurationError(f"network {network_id} has no processed snapshot")
    elif selector == LATEST_COLLECTED:
        snapshot_id = client.snapshots.latest_collected_id(
            network_id, scan_limit=settings.latest_collected_scan_limit
        )
    else:
        snapshot_id = selector

    try:
        metrics = client.snapshots.metrics(snapshot_id)
        completeness = completeness_from_metrics(metrics)
    except ForwardError as exc:
        logger.warning("snapshot %s metrics unavailable: %s", snapshot_id, exc)
        completeness = completeness_from_metrics(None, available=False)

    return ResolvedSnapshot(id=str(snapshot_id), selector=selector, completeness=completeness)

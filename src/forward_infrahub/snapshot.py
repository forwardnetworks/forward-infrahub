"""Snapshot selection and the completeness gate."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from forward_sdk import ForwardConfigurationError, ForwardError
from forward_sdk.nqe.where import enum_one_of, one_of

from forward_infrahub.nqe_types import DEVICE_TYPE, VENDOR
from forward_infrahub.ports import ForwardPort
from forward_infrahub.scope import scope_token
from forward_infrahub.settings import LATEST_COLLECTED, LATEST_PROCESSED, AdapterSettings

__all__ = [
    "ResolvedSnapshot",
    "collected_probe_scope",
    "completeness_from_metrics",
    "resolve_snapshot",
]

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


def collected_probe_scope(settings: AdapterSettings) -> str:
    """Narrow the latestCollected probe to the devices this sync owns.

    Unscoped, the probe asks whether the network collected anything at all,
    which can answer yes for a snapshot in which every device this sync cares
    about failed.

    The clauses must select the same devices as
    :class:`~forward_infrahub.scope.ForwardDeviceScope`, or the run would pick a
    snapshot on the strength of devices it then discards. The settings have
    already resolved each allowlist entry to a real enum member, so nothing here
    has to reason about how a member name is spelled.

    Vendor and device type are enums, and NQE compares an enum against a named
    constant rather than a string; the string form is rejected outright. Model
    is a genuine string, so it uses the list form and is lower-cased to match
    the scope's own comparison.
    """
    clauses: list[str] = []
    enum_fields = (
        ("device.platform.vendor", VENDOR, settings.device_vendors),
        ("device.platform.deviceType", DEVICE_TYPE, settings.device_types),
    )
    for expression, enum_type, values in enum_fields:
        predicate = enum_one_of(expression, enum_type, sorted(set(values)))
        if predicate:
            clauses.append(f"where {predicate}")

    models = sorted({scope_token(value) for value in settings.device_models if str(value).strip()})
    model_predicate = one_of("toLowerCase(device.platform.model)", models)
    if model_predicate:
        clauses.append(f"where {model_predicate}")
    return "\n".join(clauses)


def resolve_snapshot(
    forward: ForwardPort, settings: AdapterSettings, network_id: str
) -> ResolvedSnapshot:
    """Turn the configured selector into one pinned snapshot id plus its completeness."""
    selector = (settings.snapshot_id or LATEST_PROCESSED).strip() or LATEST_PROCESSED
    if selector == LATEST_PROCESSED:
        snapshot_id = forward.latest_processed_snapshot(network_id)
        if not snapshot_id:
            raise ForwardConfigurationError(f"network {network_id} has no processed snapshot")
    elif selector == LATEST_COLLECTED:
        snapshot_id = forward.latest_collected_snapshot(
            network_id,
            scan_limit=settings.latest_collected_scan_limit,
            where=collected_probe_scope(settings),
        )
    else:
        snapshot_id = selector

    try:
        metrics = forward.snapshot_metrics(snapshot_id)
        completeness = completeness_from_metrics(metrics)
    except ForwardError as exc:
        logger.warning("snapshot %s metrics unavailable: %s", snapshot_id, exc)
        completeness = completeness_from_metrics(None, available=False)

    return ResolvedSnapshot(id=str(snapshot_id), selector=selector, completeness=completeness)

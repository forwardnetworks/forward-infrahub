"""Snapshot diffs behind infrahub-sync's incremental cursors.

infrahub-sync stores a timestamp cursor per resource and decides for a whole
side whether to reuse the previous run's snapshot. Forward's own incremental
unit is different: a diff of one committed query between two snapshots. The
bridge is a sidecar written after a clean run, naming the snapshot, the scope
and the query commits that run used. A later run may diff against it only when
all three still match, so a moved scope or a republished query restarts from a
full load rather than reporting a wrong delta.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from forward_sdk import ForwardError

from forward_infrahub.exceptions import ForwardIncrementalError
from forward_infrahub.fetch import ResolvedQuery

Row = dict[str, Any]


@dataclass(frozen=True)
class BaselineSidecar:
    """What the previous clean run loaded, so a diff knows what it is diffing from."""

    snapshot_id: str
    scope_fingerprint: str
    query_commits: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> BaselineSidecar | None:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or not payload.get("snapshot_id"):
            return None
        return cls(
            snapshot_id=str(payload["snapshot_id"]),
            scope_fingerprint=str(payload.get("scope_fingerprint") or ""),
            query_commits={str(k): str(v) for k, v in (payload.get("query_commits") or {}).items()},
        )

    def save(self, path: Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")


def incremental_reason(
    *,
    queries: Iterable[ResolvedQuery],
    sidecar: BaselineSidecar | None,
    snapshot_id: str,
    fingerprint: str,
) -> str:
    """Why this run cannot be incremental, or "" when it can."""
    inline = sorted(q.label for q in queries if not q.diff_capable)
    if inline:
        return f"queries not published: {', '.join(inline)}"
    if sidecar is None:
        return "no baseline from a previous run"
    if sidecar.scope_fingerprint != fingerprint:
        return "the ownership scope or schema mapping changed since the last run"
    current = {q.label: q.commit_id for q in queries}
    republished = sorted(
        label
        for label, commit in current.items()
        if sidecar.query_commits.get(label, commit) != commit
    )
    if republished:
        return f"queries republished since the baseline: {', '.join(republished)}"
    if not snapshot_id:
        return "no snapshot resolved"
    return ""


def diff_rows(
    *,
    client: Any,
    queries: Sequence[ResolvedQuery],
    baseline_snapshot_id: str,
    snapshot_id: str,
) -> list[Row]:
    """Rows a snapshot diff reports as added or changed.

    Deleted rows are not returned: infrahub-sync detects removals by comparing
    the hydrated previous snapshot with what this run reports, so a row that
    simply stops being mentioned is handled there.
    """
    rows: list[Row] = []
    for query in queries:
        if not query.diff_capable:
            raise ForwardIncrementalError(
                f"query {query.label!r} is not published, so it cannot be diffed"
            )
        try:
            entries = client.nqe.diff(query.ref, before=baseline_snapshot_id, after=snapshot_id)
        except ForwardError as exc:
            raise ForwardIncrementalError(
                f"diff of {query.label!r} between {baseline_snapshot_id} and {snapshot_id} "
                f"failed: {exc}"
            ) from exc
        for entry in entries:
            # Presence of `after` is what distinguishes an added or changed row
            # from a deleted one, rather than the entry's type, because
            # infrahub-sync finds deletions by comparing against its own cache.
            if entry.after:
                rows.append(dict(entry.after))
    return rows

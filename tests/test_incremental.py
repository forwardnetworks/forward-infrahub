"""When a run may read a snapshot diff instead of everything."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from forward_sdk import ForwardError
from forward_sdk.models import NqeDiffEntry
from forward_sdk.nqe import QueryRef

from forward_infrahub.exceptions import ForwardIncrementalError
from forward_infrahub.fetch import ResolvedQuery
from forward_infrahub.incremental import BaselineSidecar, diff_rows, incremental_reason

PUBLISHED = ResolvedQuery(
    key="file:forward_devices.nqe",
    ref=QueryRef.by_id("FQ_devices", commit_id="commit-1"),
    diff_capable=True,
    label="forward_devices.nqe",
)
INLINE = ResolvedQuery(
    key="file:forward_vlans.nqe",
    ref=QueryRef.inline("foreach d in network.devices select {}"),
    diff_capable=False,
    label="forward_vlans.nqe",
)
SIDECAR = BaselineSidecar(
    snapshot_id="snap-1",
    scope_fingerprint="fp",
    query_commits={"forward_devices.nqe": "commit-1"},
)


def reason(**kwargs: Any) -> str:
    args: dict[str, Any] = {
        "queries": [PUBLISHED],
        "sidecar": SIDECAR,
        "snapshot_id": "snap-2",
        "fingerprint": "fp",
    }
    args.update(kwargs)
    return incremental_reason(**args)


def test_a_published_query_with_an_intact_baseline_may_diff() -> None:
    assert reason() == ""


def test_an_unpublished_query_cannot_diff() -> None:
    assert "not published" in reason(queries=[PUBLISHED, INLINE])


def test_a_first_run_has_nothing_to_diff_against() -> None:
    assert "no baseline" in reason(sidecar=None)


def test_a_moved_scope_invalidates_the_baseline() -> None:
    """Different devices in scope means the previous rows were a different set."""
    assert "scope" in reason(fingerprint="different")


def test_a_republished_query_invalidates_the_baseline() -> None:
    """New query source means the old rows answered a different question."""
    moved = BaselineSidecar("snap-1", "fp", {"forward_devices.nqe": "commit-0"})
    assert "republished" in reason(sidecar=moved)


def test_sidecar_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "forward-baseline.json"
    SIDECAR.save(path)
    assert BaselineSidecar.load(path) == SIDECAR


def test_a_missing_or_broken_sidecar_reads_as_absent(tmp_path: Path) -> None:
    assert BaselineSidecar.load(tmp_path / "nope.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert BaselineSidecar.load(broken) is None
    empty = tmp_path / "empty.json"
    empty.write_text("{}")
    assert BaselineSidecar.load(empty) is None


class FakeNqe:
    def __init__(
        self, entries: list[dict[str, Any]] | None = None, *, raises: bool = False
    ) -> None:
        self._entries = entries or []
        self._raises = raises
        self.calls: list[tuple[str, str]] = []

    def diff(self, query: Any, *, before: str, after: str, **_: Any) -> list[NqeDiffEntry]:
        if self._raises:
            raise ForwardError("diff exploded")
        self.calls.append((before, after))
        return [NqeDiffEntry.model_validate(entry) for entry in self._entries]


class FakeClient:
    def __init__(self, nqe: FakeNqe) -> None:
        self.nqe = nqe


def test_diff_returns_added_and_changed_rows() -> None:
    entries: list[dict[str, Any]] = [
        {"type": "ADDED", "before": None, "after": {"name": "new"}},
        {"type": "MODIFIED", "before": {"name": "old"}, "after": {"name": "changed"}},
        {"type": "DELETED", "before": {"name": "gone"}, "after": None},
    ]
    rows = diff_rows(
        client=FakeClient(FakeNqe(entries)),
        queries=[PUBLISHED],
        baseline_snapshot_id="snap-1",
        snapshot_id="snap-2",
    )
    assert [row["name"] for row in rows] == ["new", "changed"]


def test_diffing_an_unpublished_query_is_refused() -> None:
    with pytest.raises(ForwardIncrementalError, match="not published"):
        diff_rows(
            client=FakeClient(FakeNqe()),
            queries=[INLINE],
            baseline_snapshot_id="snap-1",
            snapshot_id="snap-2",
        )


def test_a_failed_diff_is_never_quietly_a_full_load() -> None:
    """Falling back would report a delta the operator cannot tell from a real one."""
    with pytest.raises(ForwardIncrementalError, match="failed"):
        diff_rows(
            client=FakeClient(FakeNqe(raises=True)),
            queries=[PUBLISHED],
            baseline_snapshot_id="snap-1",
            snapshot_id="snap-2",
        )


def test_a_clean_load_records_a_baseline(adapter: Any, tmp_path: Path) -> None:
    """Without a baseline no later run can diff, so the load must leave one."""
    sidecar = BaselineSidecar.load(adapter._sidecar_path())
    assert sidecar is not None
    assert sidecar.snapshot_id == "snap-2"
    assert sidecar.scope_fingerprint == adapter.scope_fingerprint


def test_existing_ids_report_what_the_source_currently_has(adapter: Any) -> None:
    assert set(adapter.list_existing_ids("DcimDevice")) == {"device-001", "device-002"}


def test_two_syncs_keep_separate_baselines(adapter: Any) -> None:
    """Two Forward networks into one Infrahub must not overwrite each other."""
    assert adapter.config.name in adapter._sidecar_path().name


def test_a_query_resolved_without_a_commit_is_refused() -> None:
    """Unpinned means diffed against head, so a rewrite would go unnoticed."""
    unpinned = ResolvedQuery(
        key="file:forward_devices.nqe",
        ref=QueryRef.by_id("FQ_devices"),
        diff_capable=True,
        label="forward_devices.nqe",
    )
    assert "without a commit" in reason(queries=[unpinned])


def test_a_normal_published_run_trips_no_guard() -> None:
    """The unpinned guard must fire on a real problem, not on every healthy run."""
    assert reason() == ""
    assert PUBLISHED.commit_id == "commit-1"


def test_a_resolved_path_keeps_its_commit() -> None:
    """A by_path query carries its pin through resolution, so the guard stays quiet.

    Regression test for an SDK bug that dropped the commit on every path
    resolution, which silently reduced the republished-query check to comparing
    empty against empty.
    """
    resolved = QueryRef.by_path("/forward_infrahub/forward_devices").resolved(
        "FQ_devices", "commit-1"
    )
    query = ResolvedQuery(
        key="file:forward_devices.nqe",
        ref=resolved,
        diff_capable=True,
        label="forward_devices.nqe",
    )
    assert query.commit_id == "commit-1"
    assert reason(queries=[query]) == ""

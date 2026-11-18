"""The cached rows a diff is folded into, and when a run may use them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from forward_sdk.models import NqeDiffEntry
from forward_sdk.nqe import QueryRef

from forward_infrahub.fetch import ResolvedQuery
from forward_infrahub.rowcache import (
    Baseline,
    RowCache,
    apply_diff,
    full_fetch_reason,
)

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
BASELINE = Baseline(
    snapshot_id="snap-1",
    scope_fingerprint="fp",
    query_commits={"forward_devices.nqe": "commit-1"},
    tables={"file:forward_devices.nqe": 2},
)


def reason(**kwargs: Any) -> str:
    args: dict[str, Any] = {
        "queries": [PUBLISHED],
        "baseline": BASELINE,
        "fingerprint": "fp",
        "incremental": True,
        "full_refetch_every": 20,
    }
    args.update(kwargs)
    return full_fetch_reason(**args)


def test_a_published_query_with_an_intact_baseline_may_diff() -> None:
    assert reason() == ""


def test_an_unpublished_query_forces_a_full_fetch() -> None:
    assert "not published" in reason(queries=[PUBLISHED, INLINE])


def test_a_first_run_has_nothing_to_diff_against() -> None:
    assert "no baseline" in reason(baseline=None)


def test_a_moved_scope_invalidates_the_baseline() -> None:
    """Different devices in scope means the previous rows were a different set."""
    assert "scope" in reason(fingerprint="different")


def test_a_republished_query_invalidates_the_baseline() -> None:
    """New query source means the old rows answered a different question."""
    moved = Baseline("snap-1", "fp", {"forward_devices.nqe": "commit-0"})
    assert "republished" in reason(baseline=moved)


def test_a_query_resolved_without_a_commit_is_refused() -> None:
    """Unpinned means diffed against head, so a rewrite would go unnoticed."""
    unpinned = ResolvedQuery(
        key="file:forward_devices.nqe",
        ref=QueryRef.by_id("FQ_devices"),
        diff_capable=True,
        label="forward_devices.nqe",
    )
    assert "without a commit" in reason(queries=[unpinned])


def test_the_cadence_forces_a_periodic_full_fetch() -> None:
    """Diffs applied to diffs would carry any drift forward for ever."""
    stale = Baseline("snap-1", "fp", {"forward_devices.nqe": "commit-1"}, runs_since_full=20)
    assert "since the last full fetch" in reason(baseline=stale)
    assert reason(baseline=stale, full_refetch_every=0) == ""


def test_incremental_off_means_always_full() -> None:
    assert reason(incremental=False) == "incremental is off"


def test_rows_and_baseline_round_trip(tmp_path: Path) -> None:
    cache = RowCache(tmp_path, "from-forwardnetworks")
    rows = [{"name": "device-001", "tags": ["a"]}, {"name": "device-002", "tags": []}]
    cache.commit(BASELINE, {"file:forward_devices.nqe": rows})
    assert cache.rows("file:forward_devices.nqe") == rows
    stored = cache.baseline()
    assert stored is not None
    assert stored.snapshot_id == "snap-1"
    assert stored.tables == {"file:forward_devices.nqe": 2}


def test_a_missing_or_broken_baseline_reads_as_absent(tmp_path: Path) -> None:
    cache = RowCache(tmp_path, "sync")
    assert cache.baseline() is None
    assert cache.rows("file:forward_devices.nqe") is None
    cache.baseline_path.parent.mkdir(parents=True, exist_ok=True)
    cache.baseline_path.write_text("{not json")
    assert cache.baseline() is None
    cache.baseline_path.write_text("{}")
    assert cache.baseline() is None


def test_two_syncs_keep_separate_caches(tmp_path: Path) -> None:
    """Two Forward networks into one Infrahub must not overwrite each other."""
    assert RowCache(tmp_path, "from-network-a").root != RowCache(tmp_path, "from-network-b").root


def entries(*payloads: dict[str, Any]) -> list[NqeDiffEntry]:
    return [NqeDiffEntry.model_validate(payload) for payload in payloads]


def test_a_diff_adds_replaces_and_removes_rows() -> None:
    cached = [{"name": "device-001", "os": "1.0"}, {"name": "device-002", "os": "2.0"}]
    outcome = apply_diff(
        cached,
        entries(
            {"type": "ADDED", "before": None, "after": {"name": "device-003", "os": "3.0"}},
            {
                "type": "MODIFIED",
                "before": {"name": "device-001", "os": "1.0"},
                "after": {"name": "device-001", "os": "1.1"},
            },
            {"type": "DELETED", "before": {"name": "device-002", "os": "2.0"}, "after": None},
        ),
        ("name",),
    )
    assert {row["name"]: row["os"] for row in outcome.rows} == {
        "device-001": "1.1",
        "device-003": "3.0",
    }
    assert (outcome.added, outcome.modified, outcome.deleted) == (1, 1, 1)
    assert outcome.summary == "+1 ~1 -1"


def test_a_diff_folds_on_the_whole_primary_key() -> None:
    """Two rows differing only in the second key column are two rows, not one."""
    cached = [
        {"device": "device-001", "name": "Ethernet1", "mtu": 1500},
        {"device": "device-002", "name": "Ethernet1", "mtu": 1500},
    ]
    outcome = apply_diff(
        cached,
        entries(
            {
                "type": "MODIFIED",
                "before": {"device": "device-002", "name": "Ethernet1", "mtu": 1500},
                "after": {"device": "device-002", "name": "Ethernet1", "mtu": 9000},
            }
        ),
        ("device", "name"),
    )
    assert sorted((r["device"], r["mtu"]) for r in outcome.rows) == [
        ("device-001", 1500),
        ("device-002", 9000),
    ]


def test_a_diff_without_a_primary_key_is_refused() -> None:
    """Folding on nothing would collapse every row onto one key."""
    with pytest.raises(ValueError, match="primary key"):
        apply_diff([{"name": "a"}], [], ())

"""The fetch path end to end: full, then diff, then nothing.

Forward's results endpoint is the scarce resource here. These drive the
adapter the way two consecutive runs would and assert what it asked Forward
for, not only what it produced.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from infrahub_sync import SyncConfig

from forward_infrahub.adapter import ForwardnetworksAdapter
from forward_infrahub.exceptions import ForwardLoadError
from forward_infrahub.fetch import merge_shards, shard_source
from forward_infrahub.replay import ReplayClient

from .conftest import FIXTURE, build_models

DEVICES = "file:forward_devices.nqe"


def make(config: SyncConfig, **client_kwargs: Any) -> Any:
    """An adapter whose client is a replay client built with these options."""
    adapter = ForwardnetworksAdapter(target="infrahub", adapter=config.source, config=config)
    if client_kwargs:
        fixture = json.loads(Path((config.source.settings or {})["replay_fixture"]).read_text())
        adapter.client = ReplayClient(fixture, **client_kwargs)
    for name, model in build_models(config).items():
        setattr(adapter, name, model)
    adapter.top_level = list(config.order)  # type: ignore[misc]
    return adapter


def with_settings(config: SyncConfig, **overrides: Any) -> SyncConfig:
    settings = dict(config.source.settings or {})
    settings.update(overrides)
    config.source.settings = settings
    return config


def at_snapshot(config: SyncConfig, snapshot: str, tmp_path: Path) -> SyncConfig:
    """The same fixture, reported as a different snapshot."""
    payload = json.loads(Path((config.source.settings or {})["replay_fixture"]).read_text())
    payload["snapshot_id"] = snapshot
    target = tmp_path / f"{snapshot}.json"
    target.write_text(json.dumps(payload))
    return with_settings(config, replay_fixture=str(target))


def test_a_first_run_fetches_everything_and_records_a_baseline(adapter: Any) -> None:
    assert adapter.fetch_mode == "full"
    assert "no baseline" in adapter.fetch_reason
    baseline = adapter.cache.baseline()
    assert baseline is not None
    assert baseline.snapshot_id == "snap-2"
    assert baseline.scope_fingerprint == adapter.scope_fingerprint
    assert baseline.runs_since_full == 0
    assert adapter.cache.rows(DEVICES) is not None


def test_the_second_run_on_the_same_snapshot_asks_forward_for_nothing(
    adapter: Any, replay_config: SyncConfig
) -> None:
    """The rows cannot have changed, so neither a fetch nor a diff is warranted."""
    second = make(replay_config)
    second.load()
    assert second.fetch_mode == "unchanged"
    assert second.client.calls == []
    assert len(second.get_all("DcimDevice")) == 2


def test_a_new_snapshot_reads_only_the_diff(
    adapter: Any, replay_config: SyncConfig, tmp_path: Path
) -> None:
    """The whole point: a changed device arrives without refetching any table."""
    second = make(at_snapshot(replay_config, "snap-3", tmp_path))
    second.load()

    assert second.fetch_mode == "diff"
    assert all(call.startswith("diff:") for call in second.client.calls), second.client.calls
    assert "diff:forward_devices:snap-2->snap-3" in second.client.calls

    device = second.get("DcimDevice", "device-001")
    assert device.description == "new"
    # The DELETED entry in the fixture removes a device that the baseline
    # never had, so the table is unchanged in length.
    assert len(second.get_all("DcimDevice")) == 2
    assert second.cache.baseline().snapshot_id == "snap-3"
    assert second.cache.baseline().runs_since_full == 1


def test_a_diff_removes_the_rows_forward_stopped_reporting(
    adapter: Any, replay_config: SyncConfig, tmp_path: Path
) -> None:
    """A device that leaves the snapshot must leave the cached table too."""
    cached = adapter.cache.rows(DEVICES)
    cached.append({**cached[0], "name": "device-003", "description": "doomed"})
    adapter.cache.commit(adapter.cache.baseline(), {DEVICES: cached})

    second = make(at_snapshot(replay_config, "snap-3", tmp_path))
    second.load()
    assert {d.name for d in second.get_all("DcimDevice")} == {"device-001", "device-002"}


def test_a_failed_diff_falls_back_to_reading_the_table_whole(
    adapter: Any, replay_config: SyncConfig, tmp_path: Path
) -> None:
    """A whole table read fresh is always right; a diff that failed is not."""
    second = make(at_snapshot(replay_config, "snap-3", tmp_path))
    second.client.failing_diffs = {"forward_devices"}
    second.load()
    assert "forward_devices" in second.client.calls
    assert len(second.get_all("DcimDevice")) == 2


def test_an_incomplete_snapshot_never_becomes_the_baseline(
    adapter: Any, replay_config: SyncConfig, tmp_path: Path
) -> None:
    """Its rows understate the network; diffing from them would invent changes."""
    payload = json.loads(FIXTURE.read_text())
    payload["snapshot_id"] = "snap-3"
    payload["metrics"] = {"numCollectionFailureDevices": 2}
    target = tmp_path / "incomplete.json"
    target.write_text(json.dumps(payload))

    second = make(with_settings(replay_config, replay_fixture=str(target)))
    second.load()
    assert second.cache.baseline().snapshot_id == "snap-2"


def test_a_republished_query_forces_a_full_fetch(
    adapter: Any, replay_config: SyncConfig, tmp_path: Path
) -> None:
    baseline = adapter.cache.baseline()
    moved = {**baseline.query_commits, "forward_devices.nqe": "some-other-commit"}
    adapter.cache.commit(
        type(baseline)(
            snapshot_id=baseline.snapshot_id,
            scope_fingerprint=baseline.scope_fingerprint,
            query_commits=moved,
        ),
        {},
    )
    second = make(at_snapshot(replay_config, "snap-3", tmp_path))
    second.load()
    assert second.fetch_mode == "full"
    assert "republished" in second.fetch_reason


def test_an_unpublished_query_cannot_diff(replay_config: SyncConfig, tmp_path: Path) -> None:
    """Forward diffs a committed query; inline source has nothing to compare."""
    first = make(replay_config, unpublished=["forward_vlans"])
    first.load()
    assert first.fetch_mode == "full"

    second = make(at_snapshot(replay_config, "snap-3", tmp_path), unpublished=["forward_vlans"])
    second.load()
    assert second.fetch_mode == "full"
    assert "not published" in second.fetch_reason


def test_a_table_too_large_to_page_is_fetched_in_shards(
    replay_config: SyncConfig, caplog: pytest.LogCaptureFixture
) -> None:
    """A query that cannot be paged to completion is split by device instead.

    Not a routine path: Forward's paging works to completion in the ordinary
    case (forward-sdk>=0.1.6; earlier versions had a false-positive stall
    detector that misdiagnosed this as a 250,000-row server-side cap, which it
    never was). This is the fallback for when the row- or page-count guard
    genuinely trips, or a server genuinely never advances the offset.
    """
    # Four rows is the interfaces table exactly; every other fixture table is
    # smaller, so only that one is driven through the sharded path.
    config = with_settings(replay_config, shard_devices=1)
    adapter = make(config, paging_cap=4)
    with caplog.at_level("INFO"):
        adapter.load()

    shard_calls = [c for c in adapter.client.calls if ":shard:" in c]
    assert shard_calls, adapter.client.calls
    assert "forward_interfaces:shard:1" in shard_calls
    # Sharding is a fallback, not the normal path: it must say so.
    assert "could not be paged to completion" in caplog.text

    # The whole property: a sharded fetch loads what an unsharded one loads.
    whole = make(replay_config)
    whole.load()
    for kind in ("InterfacePhysical", "InterfaceVirtual"):
        assert {o.get_unique_id() for o in adapter.get_all(kind)} == {
            o.get_unique_id() for o in whole.get_all(kind)
        }


def test_sharding_narrows_the_query_at_its_device_loop() -> None:
    narrowed = shard_source(
        'foreach device in network.devices\nwhere device.name != ""\nselect { n: 1 }',
        ["device-001", "device-002"],
    )
    assert 'where device.name in ["device-001", "device-002"]' in narrowed
    assert narrowed.index("foreach device in network.devices") < narrowed.index(
        "where device.name in"
    )


def test_a_query_with_no_device_loop_cannot_be_sharded() -> None:
    with pytest.raises(ForwardLoadError, match="shard"):
        shard_source("foreach account in network.cloudAccounts select { n: 1 }", ["device-001"])


def test_a_devices_table_too_large_to_page_says_so_plainly(replay_config: SyncConfig) -> None:
    """Devices are the shard universe, so that one table cannot be split by device."""
    adapter = make(replay_config, paging_cap=2)
    with pytest.raises(ForwardLoadError, match="no device list"):
        adapter.load()


def test_merging_shards_unites_the_devices_each_one_saw() -> None:
    """An aggregated row appears once per shard, naming only that shard's devices."""
    merged = merge_shards(
        [
            [{"name": "Arista", "scope_devices": ["device-001"]}],
            [{"name": "Arista", "scope_devices": ["device-002"]}],
            [{"name": "Cisco", "scope_devices": ["device-003"]}],
        ],
        ("name",),
    )
    by_name = {row["name"]: row["scope_devices"] for row in merged}
    assert by_name == {"Arista": ["device-001", "device-002"], "Cisco": ["device-003"]}


def test_a_transform_does_not_rewrite_the_rows_the_cache_keeps(replay_config: SyncConfig) -> None:
    """Transforms must work on copies of the rows, not on the rows themselves.

    The row cache keeps what the queries returned. A transform that edits those
    dicts in place leaves the transformed value in the cache, and the next run on
    the same snapshot transforms it again: harmless for upper-casing, but a date
    that gets ``T12:00:00Z`` appended becomes ``...ZT12:00:00Z``.
    """
    adapter = make(replay_config)
    adapter.load()

    cached = adapter.cache.rows("file:forward_software_versions.nqe")

    assert cached is not None
    dated = next(row for row in cached if row["name"] == "ARISTA_EOS 1.2.3")
    assert dated["end_of_support"] == "2027-06-30"
    assert adapter.get("DcimSoftwareVersion", "ARISTA_EOS 1.2.3").end_of_support == (
        "2027-06-30T12:00:00Z"
    )


def test_a_second_run_on_the_same_snapshot_writes_the_same_values(
    replay_config: SyncConfig,
) -> None:
    """The run that reads the cache must produce what the run that built it did."""
    first = make(replay_config)
    first.load()
    second = make(replay_config)
    second.load()

    assert second.fetch_mode == "unchanged"
    first_version = first.get("DcimSoftwareVersion", "ARISTA_EOS 1.2.3")
    second_version = second.get("DcimSoftwareVersion", "ARISTA_EOS 1.2.3")
    assert second_version.end_of_support == first_version.end_of_support
    first_cve = first.get("SecurityVulnerability", "CVE-2026-0001")
    second_cve = second.get("SecurityVulnerability", "CVE-2026-0001")
    assert second_cve.published == first_cve.published

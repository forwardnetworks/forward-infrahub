"""The gates that stop a run from writing something it should not."""

from __future__ import annotations

from typing import Any

import pytest
from infrahub_sync import SyncConfig

from forward_infrahub.adapter import ForwardnetworksAdapter
from forward_infrahub.exceptions import ForwardSafetyError

from .conftest import build_models


def build(config: SyncConfig) -> Any:
    adapter = ForwardnetworksAdapter(target="infrahub", adapter=config.source, config=config)
    for name, model in build_models(config).items():
        setattr(adapter, name, model)
    adapter.top_level = list(config.order)  # type: ignore[misc]
    return adapter


def with_settings(config: SyncConfig, **overrides: Any) -> SyncConfig:
    settings = dict(config.source.settings or {})
    settings.update(overrides)
    config.source.settings = settings
    return config


def make_incomplete(fixture_path: Any, tmp_path: Any) -> str:
    import json
    from pathlib import Path

    payload = json.loads(Path(fixture_path).read_text())
    payload["metrics"] = {"numCollectionFailureDevices": 2}
    target = tmp_path / "incomplete.json"
    target.write_text(json.dumps(payload))
    return str(target)


def test_an_incomplete_snapshot_still_loads_when_nothing_is_deleted(
    replay_config: SyncConfig, tmp_path: Any
) -> None:
    """Missing rows are uncollected, not gone: safe to add, unsafe to remove."""
    fixture = make_incomplete((replay_config.source.settings or {})["replay_fixture"], tmp_path)
    adapter = build(with_settings(replay_config, replay_fixture=fixture))
    adapter.load()
    assert adapter.snapshot is not None
    assert not adapter.snapshot.complete
    assert len(adapter.get_all("DcimDevice")) == 2


def test_an_incomplete_snapshot_blocks_a_run_that_deletes(
    replay_config: SyncConfig, tmp_path: Any
) -> None:
    fixture = make_incomplete((replay_config.source.settings or {})["replay_fixture"], tmp_path)
    config = with_settings(replay_config, replay_fixture=fixture)
    config.diffsync_flags = []
    with pytest.raises(ForwardSafetyError, match=r"were never|not gone upstream"):
        build(config).load()


def test_deletes_are_permitted_when_the_operator_accepts_the_risk(
    replay_config: SyncConfig, tmp_path: Any
) -> None:
    fixture = make_incomplete((replay_config.source.settings or {})["replay_fixture"], tmp_path)
    config = with_settings(
        replay_config, replay_fixture=fixture, allow_incomplete_snapshot_deletes=True
    )
    config.diffsync_flags = []
    build(config).load()


def test_require_complete_snapshot_refuses_regardless_of_deletes(
    replay_config: SyncConfig, tmp_path: Any
) -> None:
    fixture = make_incomplete((replay_config.source.settings or {})["replay_fixture"], tmp_path)
    config = with_settings(replay_config, replay_fixture=fixture, require_complete_snapshot=True)
    with pytest.raises(ForwardSafetyError, match="require_complete_snapshot"):
        build(config).load()


def test_a_complete_snapshot_permits_deletes(replay_config: SyncConfig) -> None:
    replay_config.diffsync_flags = []
    build(replay_config).load()

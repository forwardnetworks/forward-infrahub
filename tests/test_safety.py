"""The gates that stop a run from writing something it should not."""

from __future__ import annotations

import json
from pathlib import Path
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


def make_incomplete(fixture_path: Any, tmp_path: Path) -> str:
    payload = json.loads(Path(fixture_path).read_text())
    payload["metrics"] = {"numCollectionFailureDevices": 2}
    target = tmp_path / "incomplete.json"
    target.write_text(json.dumps(payload))
    return str(target)


def test_an_incomplete_snapshot_loads_with_a_warning(
    replay_config: SyncConfig, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """infrahub-sync never deletes, so an incomplete snapshot can only omit rows."""
    fixture = make_incomplete((replay_config.source.settings or {})["replay_fixture"], tmp_path)
    adapter = build(with_settings(replay_config, replay_fixture=fixture))
    with caplog.at_level("WARNING"):
        adapter.load()
    assert adapter.snapshot is not None
    assert not adapter.snapshot.complete
    assert len(adapter.get_all("DcimDevice")) == 2
    assert "collection" in caplog.text.lower()


def test_require_complete_snapshot_refuses(replay_config: SyncConfig, tmp_path: Path) -> None:
    fixture = make_incomplete((replay_config.source.settings or {})["replay_fixture"], tmp_path)
    config = with_settings(replay_config, replay_fixture=fixture, require_complete_snapshot=True)
    with pytest.raises(ForwardSafetyError, match="require_complete_snapshot"):
        build(config).load()


@pytest.mark.parametrize(
    "removed",
    ["allow_scoped_deletes", "allow_incomplete_snapshot_deletes", "publish_queries"],
)
def test_the_delete_gates_are_gone(replay_config: SyncConfig, removed: str) -> None:
    """A setting that promised a protection nothing enforced is refused, not ignored.

    infrahub-sync v3 never applies a delete, so the gates around deleting were
    removed with the promise. Accepting the keys silently would let an old
    config believe it still had them.
    """
    config = with_settings(replay_config, **{removed: True})
    with pytest.raises(ValueError, match=removed):
        build(config)

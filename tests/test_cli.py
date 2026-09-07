"""The commands around a sync."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from forward_infrahub.cli import load_settings, main
from forward_infrahub.publish import MATCHED, MISSING, STALE, audit, normalize_source
from forward_infrahub.queries import QUERY_FILENAMES, library_path, read_query_source

from .conftest import EXAMPLE_DIR


def test_check_contracts_passes_on_the_bundled_queries(capsys: Any) -> None:
    assert main(["check-contracts"]) == 0
    assert "match their contracts" in capsys.readouterr().out


def test_settings_come_from_a_sync_config() -> None:
    settings = load_settings(EXAMPLE_DIR / "config.yml")
    assert settings.query_directory == "/forward_infrahub"
    assert settings.snapshot_id == "latestProcessed"


def test_a_config_for_another_source_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "other.yml"
    config.write_text("source:\n  name: ipfabricsync\n")
    with pytest.raises(SystemExit, match="not forwardnetworks"):
        load_settings(config)


def test_no_config_falls_back_to_the_environment() -> None:
    assert load_settings(None).query_directory == "/forward_infrahub"


def test_library_paths_are_built_from_the_directory() -> None:
    assert library_path("/forward_infrahub", "forward_devices.nqe") == (
        "/forward_infrahub/forward_devices"
    )
    assert library_path("/dir/", "forward_devices.nqe") == "/dir/forward_devices"


def test_source_comparison_ignores_trailing_whitespace() -> None:
    assert normalize_source("a  \n b\n\n") == normalize_source("a\n b")
    assert normalize_source("a") != normalize_source("b")


class FakeQuery:
    def __init__(self, path: str, source: str) -> None:
        self.path = path
        self.source = source


class FakeRepo:
    def __init__(self, published: dict[str, str]) -> None:
        self._published = published

    def queries(self, *, path: str, with_source: bool = False, **_: Any) -> list[FakeQuery]:
        if path not in self._published:
            return []
        return [FakeQuery(path, self._published[path])]


class FakeClient:
    def __init__(self, published: dict[str, str]) -> None:
        self.nqe = type("Nqe", (), {"repo": FakeRepo(published)})()


def test_audit_reports_a_query_that_was_never_published() -> None:
    entries = audit(FakeClient({}), "/dir", ["forward_devices.nqe"])
    assert [e.status for e in entries] == [MISSING]


def test_audit_matches_identical_source() -> None:
    name = "forward_devices.nqe"
    published = {library_path("/dir", name): read_query_source(name)}
    entries = audit(FakeClient(published), "/dir", [name])
    assert [e.status for e in entries] == [MATCHED]


def test_audit_catches_a_query_that_drifted() -> None:
    """Present at the path is not the same as being the version we ship."""
    name = "forward_devices.nqe"
    published = {library_path("/dir", name): "foreach d in network.devices select {}"}
    entries = audit(FakeClient(published), "/dir", [name])
    assert [e.status for e in entries] == [STALE]


def test_audit_covers_every_bundled_query() -> None:
    published = {library_path("/dir", n): read_query_source(n) for n in QUERY_FILENAMES}
    entries = audit(FakeClient(published), "/dir")
    assert len(entries) == len(QUERY_FILENAMES)
    assert all(entry.ok for entry in entries)

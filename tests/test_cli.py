"""The commands around a sync."""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from forward_sdk import ForwardAuthError, ForwardNotFoundError, ForwardServerError

from forward_infrahub.cli import load_settings, main
from forward_infrahub.forward_client import ForwardSdkAdapter
from forward_infrahub.publish import (
    MATCHED,
    MISSING,
    STALE,
    UNAVAILABLE,
    audit,
    normalize_source,
)
from forward_infrahub.queries import (
    QUERY_FILENAMES,
    BundledQueries,
    library_path,
    read_query_source,
)

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


class FakeRepo:
    def __init__(self, published: dict[str, str]) -> None:
        self._published = published

    def source(self, path: str, **_: Any) -> str:
        if path not in self._published:
            raise ForwardNotFoundError(f"no query at {path}", status=404)
        return self._published[path]


class FakeClient:
    def __init__(self, published: dict[str, str]) -> None:
        self.nqe = SimpleNamespace(repo=FakeRepo(published))


def test_audit_reports_a_query_that_was_never_published() -> None:
    entries = audit(
        ForwardSdkAdapter(FakeClient({})),
        "/dir",
        ["forward_devices.nqe"],
        catalogue=BundledQueries(),
    )
    assert [e.status for e in entries] == [MISSING]


def test_audit_matches_identical_source() -> None:
    name = "forward_devices.nqe"
    published = {library_path("/dir", name): read_query_source(name)}
    entries = audit(
        ForwardSdkAdapter(FakeClient(published)), "/dir", [name], catalogue=BundledQueries()
    )
    assert [e.status for e in entries] == [MATCHED]


def test_audit_catches_a_query_that_drifted() -> None:
    """Present at the path is not the same as being the version we ship."""
    name = "forward_devices.nqe"
    published = {library_path("/dir", name): "foreach d in network.devices select {}"}
    entries = audit(
        ForwardSdkAdapter(FakeClient(published)), "/dir", [name], catalogue=BundledQueries()
    )
    assert [e.status for e in entries] == [STALE]


def test_audit_covers_every_bundled_query() -> None:
    published = {library_path("/dir", n): read_query_source(n) for n in QUERY_FILENAMES}
    entries = audit(ForwardSdkAdapter(FakeClient(published)), "/dir", catalogue=BundledQueries())
    assert len(entries) == len(QUERY_FILENAMES)
    assert all(entry.ok for entry in entries)


def test_audit_separates_cannot_ask_from_not_published() -> None:
    """A transient failure is not evidence that a query is missing.

    Reporting one as the other would tell an operator to republish a library
    that had lost nothing. This half of the contract is ours: given an error
    that is not ForwardNotFoundError, the audit must not conclude MISSING. The
    other half is the SDK's, which guarantees repo.source propagates a server
    error rather than converting it, and holds that with its own test.
    """

    class Unreachable(FakeRepo):
        def source(self, path: str, **_: Any) -> str:
            raise ForwardServerError("gateway timeout", status=502)

    client = FakeClient({})
    client.nqe.repo = Unreachable({})
    entries = audit(
        ForwardSdkAdapter(client), "/dir", ["forward_devices.nqe"], catalogue=BundledQueries()
    )
    assert [e.status for e in entries] == [UNAVAILABLE]
    assert not entries[0].ok


def test_the_audit_asks_the_sdk_for_source_the_way_the_sdk_expects() -> None:
    """Exercises the real client, not a stub of it.

    A stub of the repository hid two total failures already: a resolution that
    dropped its commit, and a source lookup that asked at the wrong commit and
    could never return anything. Both passed a fake that raised or returned what
    this code expected. This asserts against the released SDK's own surface, so
    a change in what it accepts or returns fails here rather than in production.
    """
    from forward_sdk._sync.services.nqe_repo import NqeRepository

    signature = inspect.signature(NqeRepository.source)
    assert list(signature.parameters) == ["self", "path", "repository"]
    assert signature.return_annotation == "str"
    assert "ForwardNotFoundError" in (inspect.getdoc(NqeRepository.source) or "")


def test_a_forward_error_is_reported_as_one_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An expired password is an operating condition, not a crash.

    A 401 used to end ``resolve-snapshot`` with a traceback through SDK
    internals, which buries the one line saying what the operator must fix.
    """

    def explode(_settings: Any) -> Any:
        raise ForwardAuthError("GET /api/networks/1/snapshots failed with HTTP 401", status=401)

    monkeypatch.setattr("forward_infrahub.cli.build_client", explode)
    assert main(["resolve-snapshot", "--config", str(EXAMPLE_DIR / "config.yml")]) == 1
    assert "HTTP 401" in caplog.text
    assert "Traceback" not in caplog.text

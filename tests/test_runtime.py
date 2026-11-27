"""The package built from a sync config, and the one transform that needs the engine to prove."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from infrahub_sync.configuration import parse_configuration_package
from infrahub_sync.runtime_schema import worker

from forward_infrahub.exceptions import InvalidConfiguration
from forward_infrahub.runtime import package_payload, prepare

CONFIG: dict[str, Any] = {
    "name": "from-forwardnetworks",
    "source": {"name": "forwardnetworks", "settings": {"network_id": "demo", "password": "p-1"}},
    "destination": {
        "name": "infrahub",
        "settings": {"url": "http://localhost:8000", "token": "t-1"},
    },
    "schema_mapping": [],
}


@pytest.fixture(autouse=True)
def _no_ambient_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [k for k in os.environ if k.startswith(("INFRAHUB_", "FORWARD_"))]:
        monkeypatch.delenv(name)


def test_the_package_holds_no_secret_and_infrahub_sync_parses_it() -> None:
    payload = package_payload(CONFIG)

    assert "credentials" not in payload
    assert "p-1" not in str(payload) and "t-1" not in str(payload)
    assert payload["configuration"]["destination"]["credential"] == "infrahub"
    parse_configuration_package(payload)


def test_building_the_package_changes_neither_the_environment_nor_the_config() -> None:
    environment, before = dict(os.environ), str(CONFIG)

    package_payload(CONFIG)

    assert dict(os.environ) == environment
    assert str(CONFIG) == before


def test_a_credential_reference_is_refused_with_what_to_do_instead() -> None:
    source = {"name": "forwardnetworks", "settings": {"password": {"$credential": "x"}}}

    with pytest.raises(InvalidConfiguration, match=r"source\.settings\.password.*Replace it"):
        package_payload({**CONFIG, "source": source})


def _write(root: Path, config: dict[str, Any]) -> Path:
    path = root / "syncs" / "one" / "config.yml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_preparing_a_run_reads_the_schema_through_infrahub_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Everything prepare uses of infrahub-sync is real except the network read."""
    read: list[str] = []

    def schema(package: Any, branch: str) -> dict[str, Any]:
        read.append(branch)
        return {}

    monkeypatch.setattr(worker, "read_destination_schema_snapshot", schema)

    run = prepare(_write(tmp_path, CONFIG), "from-forwardnetworks", "a-branch")

    assert read == ["a-branch"]
    assert run.instance.destination.settings == CONFIG["destination"]["settings"]


@pytest.mark.parametrize(
    ("change", "place"),
    [
        ({"source": "forwardnetworks"}, "source must be a mapping"),
        ({"source": "no-credential"}, "source must be a mapping"),
        ({"destination": [1]}, "destination must be a mapping"),
        ({"source": {"name": "forwardnetworks", "settings": "abc"}}, "source.settings must be"),
        ({"destination": {"name": "infrahub", "settings": [1]}}, "destination.settings must be"),
        ({"destination": 5}, "destination must be a mapping"),
        ({"$credential": "x"}, "the top level"),
    ],
)
def test_a_badly_shaped_config_is_refused_with_its_place(
    tmp_path: Path, change: dict[str, Any], place: str
) -> None:
    with pytest.raises(InvalidConfiguration, match=place):
        prepare(_write(tmp_path, {**CONFIG, **change}), "from-forwardnetworks", "main")


def test_a_yaml_alias_loop_does_not_recurse_forever() -> None:
    loop: list[Any] = []
    loop.append(loop)

    package_payload({**CONFIG, "extra": loop, "schema_mapping": []})


def _mac_transform_expression() -> str:
    from pathlib import Path

    import yaml

    config = yaml.safe_load(
        (
            Path(__file__).parent.parent / "examples/forwardnetworks_to_infrahub/config.yml"
        ).read_text()
    )
    physical = next(m for m in config["schema_mapping"] if m["name"] == "InterfacePhysical")
    return str(next(t["expression"] for t in physical["transforms"] if t["field"] == "mac_address"))


def test_an_interface_without_a_mac_stays_null_and_one_with_a_mac_is_upper_cased() -> None:
    """Infrahub's MacAddress rejects an empty string, and `none | upper` is the text NONE."""
    from infrahub_sync import DiffSyncModelMixin

    expression = _mac_transform_expression()
    absent = {"mac_address": None}
    present = {"mac_address": "aa:bb:cc:00:57:31"}

    DiffSyncModelMixin.apply_transform(absent, expression, "mac_address")
    DiffSyncModelMixin.apply_transform(present, expression, "mac_address")

    assert absent["mac_address"] is None
    assert present["mac_address"] == "AA:BB:CC:00:57:31"

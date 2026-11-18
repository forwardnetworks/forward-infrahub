"""A registered package may only name credentials inside infrahub-sync's namespace."""

from __future__ import annotations

import pytest
from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.configuration.credentials import ENV_CREDENTIAL_PREFIX

from forward_infrahub.runtime import package_payload

CONFIG = {
    "name": "from-forwardnetworks",
    "source": {"name": "forwardnetworks", "settings": {"network_id": "demo"}},
    "destination": {"name": "infrahub", "settings": {"url": "http://localhost:8000"}},
    "schema_mapping": [],
}


NAMESPACED = tuple(
    f"{ENV_CREDENTIAL_PREFIX}{name}"
    for name in ("FORWARD_USERNAME", "FORWARD_PASSWORD", "INFRAHUB_TOKEN")
)


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """package_payload writes os.environ; set-then-delete so teardown removes it."""
    for name in ("FORWARD_USERNAME", "FORWARD_PASSWORD", "INFRAHUB_API_TOKEN", *NAMESPACED):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


def test_every_credential_names_an_environment_variable_inside_the_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORWARD_USERNAME", "u")
    monkeypatch.setenv("FORWARD_PASSWORD", "p")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")

    payload = package_payload(CONFIG)

    identifiers = {c["identifier"] for c in payload["credentials"].values()}
    assert identifiers
    assert all(i.startswith(ENV_CREDENTIAL_PREFIX) for i in identifiers)


def test_the_payload_is_accepted_by_infrahub_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check that was missing: the engine's own validation, not ours."""
    monkeypatch.setenv("FORWARD_USERNAME", "u")
    ConfigurationPackage.model_validate(package_payload(CONFIG))


def test_a_value_exported_under_the_ordinary_name_reaches_the_namespaced_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    monkeypatch.setenv("INFRAHUB_API_TOKEN", "from-the-environment")

    payload = package_payload(CONFIG)

    variable = payload["credentials"]["infrahub-token"]["identifier"]
    assert os.environ[variable] == "from-the-environment"


def test_a_stale_namespaced_variable_does_not_beat_this_runs_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    monkeypatch.setenv(NAMESPACED[2], "left-over-from-another-run")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "current")

    payload = package_payload(CONFIG)

    assert os.environ[payload["credentials"]["infrahub-token"]["identifier"]] == "current"


def test_a_literal_value_in_the_config_is_used_in_place_of_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    monkeypatch.setenv("FORWARD_PASSWORD", "from-the-environment")
    config = {**CONFIG, "source": {"name": "forwardnetworks", "settings": {"password": "literal"}}}

    payload = package_payload(config)

    assert os.environ[payload["credentials"]["forward-password"]["identifier"]] == "literal"


def _mac_transform_expression() -> str:
    from pathlib import Path

    import yaml

    config = yaml.safe_load(
        (
            Path(__file__).parent.parent / "examples/forwardnetworks_to_infrahub/config.yml"
        ).read_text()
    )
    physical = next(m for m in config["schema_mapping"] if m["name"] == "InterfacePhysical")
    return next(t["expression"] for t in physical["transforms"] if t["field"] == "mac_address")


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

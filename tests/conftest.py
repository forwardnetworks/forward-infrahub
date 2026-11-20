"""Shared fixtures: the example config, the row fixture, and generated models."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from infrahub_sync import SyncConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DIR = REPO_ROOT / "examples" / "forwardnetworks_to_infrahub"
FIXTURE = Path(__file__).parent / "fixtures" / "forward_rows.json"

#: Identifiers and list-valued fields per kind, as `infrahub-sync generate`
#: would derive them from a live Infrahub schema. Hand-written so the adapter
#: can be tested without a running Infrahub.
MODEL_SHAPES: dict[str, dict[str, Any]] = {
    "OrganizationManufacturer": {"ids": ("name",), "lists": ()},
    "DcimPlatform": {"ids": ("name",), "lists": ()},
    "DcimDeviceType": {"ids": ("name",), "lists": ()},
    "LocationSite": {"ids": ("name",), "lists": ()},
    "BuiltinTag": {"ids": ("name",), "lists": ()},
    "DcimDevice": {"ids": ("name",), "lists": ("tags",)},
    "InterfaceLag": {"ids": ("device", "name"), "lists": ("tagged_vlan",)},
    "InterfacePhysical": {"ids": ("device", "name"), "lists": ("tagged_vlan",)},
    "InterfaceVirtual": {"ids": ("device", "name"), "lists": ("tagged_vlan",)},
    "IpamNamespace": {"ids": ("name",), "lists": ()},
    "IpamVRF": {"ids": ("name",), "lists": ()},
    "IpamVLANGroup": {"ids": ("name",), "lists": ()},
    "IpamVLAN": {"ids": ("name",), "lists": ()},
    "IpamPrefix": {"ids": ("prefix", "ip_namespace"), "lists": ()},
    "IpamIPAddress": {"ids": ("address", "ip_namespace"), "lists": ("primary_of",)},
    "DcimCable": {"ids": ("label",), "lists": ("connected_endpoints",)},
    "DcimSoftwareVersion": {"ids": ("name",), "lists": ()},
    "SecurityVulnerability": {"ids": ("cve_id",), "lists": ("devices",)},
    "CloudAccount": {"ids": ("cloud_type", "account_id"), "lists": ()},
    "CloudVPC": {"ids": ("account_key", "network_id"), "lists": ("cidrs",)},
    "CloudSubnet": {"ids": ("vpc_key", "network_id"), "lists": ("cidrs",)},
    "CloudService": {"ids": ("vpc_key", "service_id"), "lists": ()},
}


def build_models(config: SyncConfig) -> dict[str, type]:
    """Model classes shaped like the ones `infrahub-sync generate` writes."""
    from forward_infrahub.adapter import ForwardnetworksModel

    models: dict[str, type] = {}
    # A kind may be mapped by more than one entry; one model class serves them
    # all, and its attributes are the union of what the entries set.
    fields_by_kind: dict[str, list[Any]] = {}
    for element in config.schema_mapping:
        for field in element.fields:
            known = fields_by_kind.setdefault(element.name, [])
            if all(field.name != existing.name for existing in known):
                known.append(field)

    for name, element_fields in fields_by_kind.items():
        shape = MODEL_SHAPES[name]
        ids = tuple(shape["ids"])
        attributes = tuple(f.name for f in element_fields if f.name not in ids)
        annotations: dict[str, Any] = {}
        namespace: dict[str, Any] = {
            "_modelname": name,
            "_identifiers": ids,
            "_attributes": attributes,
            "__annotations__": annotations,
        }
        for field in element_fields:
            if field.name in shape["lists"]:
                annotations[field.name] = list[Any] | None
                namespace[field.name] = None
            else:
                annotations[field.name] = Any
                namespace[field.name] = None
        models[name] = type(name, (ForwardnetworksModel,), namespace)
    return models


@pytest.fixture
def example_config() -> SyncConfig:
    return SyncConfig(**yaml.safe_load((EXAMPLE_DIR / "config.yml").read_text()))


@pytest.fixture
def replay_config(example_config: SyncConfig, tmp_path: Path) -> SyncConfig:
    """The example config, pointed at the row fixture instead of a live Forward."""
    settings = dict(example_config.source.settings or {})
    settings.update(
        {
            "replay_fixture": str(FIXTURE),
            "network_id": "test-net",
            "url": "https://example.invalid",
            "cache_directory": str(tmp_path / "cache"),
        }
    )
    example_config.source.settings = settings
    return example_config


@pytest.fixture
def adapter(replay_config: SyncConfig) -> Any:
    """A loaded adapter, with generated-style models bound to it."""
    from forward_infrahub.adapter import ForwardnetworksAdapter

    instance = ForwardnetworksAdapter(
        target="infrahub", adapter=replay_config.source, config=replay_config
    )
    models = build_models(replay_config)
    for name, model in models.items():
        setattr(instance, name, model)
    # Potenda assigns top_level per instance too (diffsync declares it a ClassVar
    # but supports the override), so the tests drive the adapter the same way.
    instance.top_level = list(replay_config.order)  # type: ignore[misc]
    instance.load()
    return instance

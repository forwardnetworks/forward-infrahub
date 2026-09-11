"""Building a run: the configuration package, the models, and the engine call.

infrahub-sync v3 has no ``generate`` command. A run reads the destination
schema once and builds its DiffSync model classes in memory from it, which is
better than generated files on disk: the models cannot drift from the schema
they were built for. This module does that reading and building for a plain
``config.yml``, so a sync can be run from one machine without standing up the
Sync service.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from forward_infrahub.capabilities import register as register_capabilities
from forward_infrahub.exceptions import ForwardInfrahubError

logger = logging.getLogger(__name__)

#: Settings that name a secret, and the environment variable each reads.
SOURCE_CREDENTIALS = {"username": "FORWARD_USERNAME", "password": "FORWARD_PASSWORD"}
DESTINATION_CREDENTIALS = {"token": "INFRAHUB_API_TOKEN"}

#: Keys a registered package does not carry: v3 derives the order itself,
#: forces its own diffsync flags, and this adapter owns its incremental policy.
PACKAGE_OMITS = ("order", "diffsync_flags", "incremental", "adapters_path")


class ForwardRuntimeError(ForwardInfrahubError):
    """A run could not be assembled from the configuration and the destination."""


@dataclass(frozen=True)
class PreparedRun:
    """Everything one run needs before the engine is asked for anything."""

    instance: Any
    package: Any
    models: dict[str, Any]
    branch: str


def _credentialise(
    settings: dict[str, Any], env_by_setting: dict[str, str], prefix: str
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Replace secret-bearing settings with credential references.

    A package may not carry a secret value, only a reference to one. A literal
    left in a config file is moved into this process's environment under a
    generated name rather than being written anywhere.
    """
    out = dict(settings)
    credentials: dict[str, dict[str, str]] = {}
    for setting, default_var in env_by_setting.items():
        value = out.get(setting)
        if isinstance(value, dict) and "$credential" in value:
            continue
        name = f"{prefix}-{setting}"
        variable = default_var
        if isinstance(value, str) and value:
            variable = f"FORWARD_INFRAHUB_{prefix.upper()}_{setting.upper()}"
            os.environ.setdefault(variable, value)
        out[setting] = {"$credential": name}
        credentials[name] = {"provider": "env", "identifier": variable}
    return out, credentials


def package_payload(config: dict[str, Any]) -> dict[str, Any]:
    """The registered-package form of a sync config, secrets by reference."""
    configuration = {k: v for k, v in config.items() if k not in PACKAGE_OMITS}
    source = dict(configuration.get("source") or {})
    destination = dict(configuration.get("destination") or {})
    source["settings"], source_credentials = _credentialise(
        dict(source.get("settings") or {}), SOURCE_CREDENTIALS, "forward"
    )
    destination["settings"], destination_credentials = _credentialise(
        dict(destination.get("settings") or {}), DESTINATION_CREDENTIALS, "infrahub"
    )
    configuration["source"] = source
    configuration["destination"] = destination
    return {
        "format_version": 1,
        "configuration": configuration,
        "credentials": {**source_credentials, **destination_credentials},
    }


def build_package(config_path: Path) -> Any:
    """The ``ConfigurationPackage`` for a sync config."""
    from infrahub_sync.configuration import ConfigurationPackage

    config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    return ConfigurationPackage.model_validate(package_payload(config))


def package_problems(package: Any) -> list[Any]:
    """Every error-severity finding infrahub-sync reports about a package."""
    from infrahub_sync.configuration import collect_findings

    # Registration must precede the check: the finding it prevents comes from
    # reading the capability table, not from loading the adapter.
    register_capabilities()
    return [f for f in collect_findings(package) if f.severity == "error"]


def prepare(config_path: Path, name: str, branch: str) -> PreparedRun:
    """Resolve the sync, check its configuration, and build its models.

    Raises:
        ForwardRuntimeError: the configuration is refused, or the destination
            schema cannot be read or does not declare what the mapping needs.
    """
    from infrahub_sync.adapters.infrahub import InfrahubAdapter, InfrahubModel
    from infrahub_sync.execution import resolve_sync_instance
    from infrahub_sync.runtime_schema.domain import normalize_destination_schema
    from infrahub_sync.runtime_schema.models import build_runtime_models
    from infrahub_sync.runtime_schema.projection import compute_consumed_schema_fingerprint
    from infrahub_sync.runtime_schema.worker import (
        RuntimeModelPlan,
        RuntimeSideModels,
        read_destination_schema_snapshot,
    )

    from forward_infrahub.adapter import ForwardnetworksAdapter, ForwardnetworksModel

    config = Path(config_path)
    instance = resolve_sync_instance(name, directory=str(config.parent.parent))
    package = build_package(config)
    problems = package_problems(package)
    if problems:
        detail = "; ".join(f"{f.location}: {f.message}" for f in problems)
        raise ForwardRuntimeError(f"the configuration is not usable: {detail}")

    try:
        snapshot = normalize_destination_schema(read_destination_schema_snapshot(package, branch))
    except Exception as exc:
        raise ForwardRuntimeError(
            f"the destination schema on branch {branch!r} could not be read: {exc}"
        ) from exc

    source_models = build_runtime_models(
        snapshot=snapshot, configuration=instance, model_base=ForwardnetworksModel
    )
    mapped = {element.name for element in instance.schema_mapping}
    missing = sorted(mapped - set(source_models))
    if missing:
        raise ForwardRuntimeError(
            f"the destination declares no usable kind for {', '.join(missing)}. Load the "
            "schema this sync targets, or take those kinds out of schema_mapping."
        )
    destination_models = build_runtime_models(
        snapshot=snapshot, configuration=instance, model_base=InfrahubModel
    )
    instance._runtime_models = RuntimeModelPlan(
        branch=branch,
        schema_fingerprint=compute_consumed_schema_fingerprint(
            configuration=instance, snapshot=snapshot
        ),
        destination=RuntimeSideModels(InfrahubAdapter, destination_models),
        source=RuntimeSideModels(ForwardnetworksAdapter, dict(source_models)),
    )
    return PreparedRun(
        instance=instance, package=package, models=dict(source_models), branch=branch
    )

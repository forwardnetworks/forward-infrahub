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

try:
    #: The only environment namespace infrahub-sync reads a credential from.
    from infrahub_sync.configuration.credentials import ENV_CREDENTIAL_PREFIX
except ImportError:  # pragma: no cover - a v3 build that predates the namespace
    ENV_CREDENTIAL_PREFIX = "INFRAHUB_SYNC_CREDENTIAL_"

#: Settings that name a secret, and the environment variable each reads.
_SOURCE_CREDENTIALS = {"username": "FORWARD_USERNAME", "password": "FORWARD_PASSWORD"}
_DESTINATION_CREDENTIALS = {"token": "INFRAHUB_API_TOKEN"}

#: Keys a registered package does not carry: v3 derives the order itself,
#: forces its own diffsync flags, and this adapter owns its incremental policy.
_PACKAGE_OMITS = ("order", "diffsync_flags", "incremental", "adapters_path")


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
        # infrahub-sync only reads a credential from its own namespace, so the
        # value an operator exported under the ordinary name (or wrote in the
        # config) is republished here, in this process, under a name inside it.
        # It overwrites: a stale value left in the shell must not beat this run's.
        variable = f"{ENV_CREDENTIAL_PREFIX}{prefix.upper()}_{setting.upper()}"
        secret = value if isinstance(value, str) and value else os.environ.get(default_var, "")
        if secret:
            os.environ[variable] = secret
        out[setting] = {"$credential": name}
        credentials[name] = {"provider": "env", "identifier": variable}
    return out, credentials


def package_payload(config: dict[str, Any]) -> dict[str, Any]:
    """The registered-package form of a sync config, secrets by reference."""
    configuration = {k: v for k, v in config.items() if k not in _PACKAGE_OMITS}
    source = dict(configuration.get("source") or {})
    destination = dict(configuration.get("destination") or {})
    source["settings"], source_credentials = _credentialise(
        dict(source.get("settings") or {}), _SOURCE_CREDENTIALS, "forward"
    )
    destination["settings"], destination_credentials = _credentialise(
        dict(destination.get("settings") or {}), _DESTINATION_CREDENTIALS, "infrahub"
    )
    configuration["source"] = source
    configuration["destination"] = destination
    return {
        "format_version": 1,
        "configuration": configuration,
        "credentials": {**source_credentials, **destination_credentials},
    }


def _build_package(config_path: Path) -> Any:
    """The ``ConfigurationPackage`` for a sync config."""
    from infrahub_sync.configuration import ConfigurationPackage

    config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    return ConfigurationPackage.model_validate(package_payload(config))


def _package_problems(package: Any) -> list[Any]:
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
    package = _build_package(config)
    problems = _package_problems(package)
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

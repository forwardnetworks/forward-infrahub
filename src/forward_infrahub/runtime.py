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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from forward_infrahub.capabilities import CREDENTIAL_SETTINGS
from forward_infrahub.capabilities import register as register_capabilities
from forward_infrahub.exceptions import ForwardInfrahubError, InvalidConfiguration

logger = logging.getLogger(__name__)

#: The setting of each adapter block that carries a secret. None of them goes into a package.
_CREDENTIAL_SETTINGS = {
    "source": CREDENTIAL_SETTINGS,
    "destination": ("token", "username", "password"),
}

#: The name the package gives the destination's credential. Only ``destination_token`` fills it.
_DESTINATION_CREDENTIAL = "infrahub"

#: What infrahub-sync reads as true in ``INFRAHUB_TLS_INSECURE``.
_TRUE = frozenset({"1", "true", "t", "yes", "y", "on"})

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


def _reference_path(node: Any, path: str, seen: set[int]) -> str | None:
    """Where the first ``$credential`` reference under ``node`` is, or ``None``.

    ``seen`` holds the containers already searched, so a YAML alias is followed once.
    """
    if id(node) in seen:
        return None
    seen.add(id(node))
    if isinstance(node, dict):
        if "$credential" in node:
            return path
        children: list[tuple[Any, Any]] = list(node.items())
    elif isinstance(node, list):
        children = list(enumerate(node))
    else:
        return None
    for key, child in children:
        found = _reference_path(child, f"{path}.{key}" if path else str(key), seen)
        if found is not None:
            return found
    return None


def _settings(config: Mapping[str, Any], side: str) -> dict[str, Any]:
    return dict((config.get(side) or {}).get("settings") or {})


def _refuse_wrong_shapes(config: Mapping[str, Any]) -> None:
    """Each adapter block, and its settings, is a mapping or left empty."""
    for side in _CREDENTIAL_SETTINGS:
        block = config.get(side)
        if block is not None and not isinstance(block, dict):
            raise InvalidConfiguration(
                f"{side} must be a mapping. Give it a name and a settings mapping."
            )
        settings = (block or {}).get("settings")
        if settings is not None and not isinstance(settings, dict):
            raise InvalidConfiguration(
                f"{side}.settings must be a mapping. Write each setting as a key and a value."
            )


def _refuse_unsupported(config: Mapping[str, Any]) -> None:
    """Refuse the forms a local run can't honour. No message repeats a value."""
    if "credentials" in config:
        raise InvalidConfiguration(
            "credentials: this block is no longer supported. Remove it, and put plain text in "
            "the adapter settings or use the FORWARD_* and INFRAHUB_API_TOKEN variables."
        )
    _refuse_wrong_shapes(config)
    reference = _reference_path(config, "", set())
    if reference is not None:
        raise InvalidConfiguration(
            f"{reference or 'the top level'}: a $credential reference is no longer supported. "
            "Replace it with plain text, or remove it and use the environment variable."
        )
    for side, keys in _CREDENTIAL_SETTINGS.items():
        block = config.get(side) or {}
        if "credential" in block:
            raise InvalidConfiguration(
                f"{side}.credential names a credential stored in the Sync API, which a local "
                "run can't receive. Remove it and use the settings or environment variables."
            )
        settings = _settings(config, side)
        for key in keys:
            value = settings.get(key)
            if not (isinstance(value, str) or value in (None, [], {})):
                raise InvalidConfiguration(
                    f"{side}.settings.{key} must be text. Put the value in quotes, or remove "
                    "it and use the environment variable."
                )


def _destination_access(settings: Mapping[str, Any]) -> tuple[str, str]:
    """The address and token, chosen as infrahub-sync chooses them for the run itself."""
    from infrahub_sync.configuration.credentials import select_runtime_credential

    url = select_runtime_credential(settings, "url", ("INFRAHUB_ADDRESS", "INFRAHUB_URL"))
    if not url:
        raise InvalidConfiguration(
            "no Infrahub address: set INFRAHUB_ADDRESS, or destination.settings.url."
        )
    token = select_runtime_credential(settings, "token", ("INFRAHUB_API_TOKEN",))
    if not token:
        raise InvalidConfiguration(
            "no Infrahub token: set INFRAHUB_API_TOKEN, or destination.settings.token."
        )
    return url, token


def package_payload(config: Mapping[str, Any]) -> dict[str, Any]:
    """The package form of a sync config: what infrahub-sync validates and reads a schema by.

    It holds no secret and no reference to one. The destination names a credential so that the
    schema read takes the token given to ``destination_token``, and the address is the one the
    run itself will use.

    Raises:
        InvalidConfiguration: the config uses a form a local run can't honour, or names no
            Infrahub address or token.
    """
    _refuse_unsupported(config)
    configuration = {k: v for k, v in config.items() if k not in _PACKAGE_OMITS}
    for side, keys in _CREDENTIAL_SETTINGS.items():
        block = dict(configuration.get(side) or {})
        block["settings"] = {k: v for k, v in _settings(config, side).items() if k not in keys}
        configuration[side] = block
    url, _token = _destination_access(_settings(config, "destination"))
    destination = configuration["destination"]
    destination["credential"] = _DESTINATION_CREDENTIAL
    destination["settings"]["url"] = url
    insecure = os.environ.get("INFRAHUB_TLS_INSECURE", "").strip().lower() in _TRUE
    if insecure and destination["settings"].get("verify_ssl") is None:
        destination["settings"]["verify_ssl"] = False
    return {"format_version": 1, "configuration": configuration}


def _read_config(path: Path) -> dict[str, Any]:
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InvalidConfiguration(
            f"{path} could not be read ({type(exc).__name__}). Point --config at a YAML file."
        ) from None
    if not isinstance(config, dict):
        raise InvalidConfiguration(f"{path} is not a mapping. Point --config at a sync config.")
    return config


def _resolve_instance(path: Path, name: str) -> Any:
    """The instance the engine will run, which must come from the file that was checked."""
    from infrahub_sync.execution import RunValidationError, resolve_sync_instance

    try:
        instance = resolve_sync_instance(name, directory=str(path.parent.parent))
    except RunValidationError as exc:
        raise InvalidConfiguration(
            f"{exc}. Point --config and --name at one valid config."
        ) from exc
    resolved = Path(instance.directory) / "config.yml"
    if resolved.resolve() != path.resolve():
        raise InvalidConfiguration(
            f"--config is {path}, but infrahub-sync would run {resolved} for {name!r}. Point "
            "--config at that file, or give each sync its own name."
        )
    return instance


def _build_package(config: Mapping[str, Any]) -> Any:
    """The ``ConfigurationPackage`` for a sync config."""
    from infrahub_sync.configuration import (
        ConfigurationPackageParseError,
        parse_configuration_package,
    )

    try:
        return parse_configuration_package(package_payload(config))
    except ConfigurationPackageParseError as exc:
        raise InvalidConfiguration(
            f"infrahub-sync refuses this configuration: {exc}. Change or remove that setting."
        ) from exc


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
        InvalidConfiguration: the config uses a form a local run can't honour, names no
            Infrahub address or token, is not the file the engine would run, or
            infrahub-sync can't parse it.
        ForwardRuntimeError: infrahub-sync reports the configuration as unusable, or the
            destination schema cannot be read or does not declare what the mapping needs.
    """
    from infrahub_sync.adapters.infrahub import InfrahubAdapter, InfrahubModel
    from infrahub_sync.configuration.capabilities import destination_token
    from infrahub_sync.runtime_schema.domain import normalize_destination_schema
    from infrahub_sync.runtime_schema.models import build_runtime_models
    from infrahub_sync.runtime_schema.projection import compute_consumed_schema_fingerprint
    from infrahub_sync.runtime_schema.worker import (
        RuntimeModelPlan,
        RuntimeSideModels,
        read_destination_schema_snapshot,
    )

    from forward_infrahub.adapter import ForwardnetworksAdapter, ForwardnetworksModel

    path = Path(config_path)
    config = _read_config(path)
    _refuse_unsupported(config)
    instance = _resolve_instance(path, name)
    package = _build_package(config)
    _url, token = _destination_access(_settings(config, "destination"))
    problems = _package_problems(package)
    if problems:
        detail = "; ".join(f"{f.location}: {f.message}" for f in problems)
        raise ForwardRuntimeError(f"the configuration is not usable: {detail}")

    try:
        with destination_token(token):
            raw = read_destination_schema_snapshot(package, branch)
        snapshot = normalize_destination_schema(raw)
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

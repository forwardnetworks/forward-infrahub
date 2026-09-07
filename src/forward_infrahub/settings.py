"""Adapter settings, validated from the ``source.settings`` block of ``config.yml``."""

from __future__ import annotations

import os
from typing import Any, Literal

from forward_sdk import ForwardClient, ForwardConfigurationError
from forward_sdk.nqe.enums import suggest
from pydantic import BaseModel, ConfigDict, Field, field_validator

from forward_infrahub import __version__
from forward_infrahub.nqe_types import DEVICE_TYPE, MEMBER_SOURCES, VENDOR, nqe_members
from forward_infrahub.replay import ReplayClient
from forward_infrahub.scope import scope_token

LATEST_PROCESSED = "latestProcessed"
LATEST_COLLECTED = "latestCollected"

ENV_URL = "FORWARD_URL"
ENV_USERNAME = "FORWARD_USERNAME"
ENV_PASSWORD = "FORWARD_PASSWORD"
ENV_NETWORK_ID = "FORWARD_NETWORK_ID"
ENV_VERIFY_TLS = "FORWARD_VERIFY_TLS"

QueryMode = Literal["auto", "published", "inline"]


def canonical_enum_values(values: Any, enum_type: str, setting: str) -> list[str]:
    """Map operator-written values onto the enum member names Forward compares.

    An allowlist is written by hand, so ``cisco``, ``CISCO`` and ``Vendor.CISCO``
    all have to mean the same thing. The member names come from the NQE
    data-model reference rather than from a rule about capitalisation, so a
    value with no member is refused here, before it becomes either a query that
    quietly matches nothing or one Forward rejects outright.
    """
    known = nqe_members(enum_type)
    if not known:
        # The data model does not describe this type. Not knowing its members
        # is not evidence about a value, so take what we were given.
        return [scope_token(value).upper() for value in values or [] if scope_token(value)]
    canonical: list[str] = []
    for value in values or []:
        token = scope_token(value).upper()
        if not token:
            continue
        if token not in known:
            close = suggest(enum_type, token)
            hint = f" Did you mean {' or '.join(close)}?" if close else ""
            raise ValueError(
                f"{setting}: {value!r} is not one of the {len(known)} NQE {enum_type} "
                f"members.{hint} The full list is at {MEMBER_SOURCES[enum_type]}; note that "
                "it differs from the REST schema of the same name that forward-sdk models."
            )
        canonical.append(token)
    return canonical


def _expand(value: Any) -> Any:
    """Expand ``${VAR}`` and ``$VAR`` in string settings so secrets can stay in the environment."""
    if isinstance(value, str):
        return os.path.expandvars(value)
    return value


class AdapterSettings(BaseModel):
    """Everything an operator can set under ``source.settings``.

    Connection values fall back to the ``FORWARD_*`` environment variables the
    Forward SDK and the NetBox and Nautobot plugins already use, so one set of
    credentials serves every integration.
    """

    model_config = ConfigDict(extra="forbid")

    url: str | None = None
    username: str | None = None
    password: str | None = None
    network_id: str | None = None
    snapshot_id: str = LATEST_PROCESSED
    latest_collected_scan_limit: int = Field(default=10, ge=1)
    verify_ssl: bool | str = True
    timeout: float = Field(default=1200.0, gt=0)
    retries: int = Field(default=2, ge=0)
    max_concurrent_queries: int = Field(default=2, ge=1)
    page_size: int = Field(default=10_000, ge=1, le=10_000)
    max_rows: int = Field(default=0, ge=0)

    device_vendors: list[str] = Field(default_factory=list)
    device_types: list[str] = Field(default_factory=list)
    device_models: list[str] = Field(default_factory=list)
    cloud_types: list[str] = Field(default_factory=list)
    cloud_account_ids: list[str] = Field(default_factory=list)

    query_directory: str = "/forward_infrahub"
    query_mode: QueryMode = "auto"
    publish_queries: bool = False

    incremental: bool = True
    require_complete_snapshot: bool = False
    allow_incomplete_snapshot_deletes: bool = False
    allow_scoped_deletes: bool = False

    normalize_location_names: bool = True
    unassigned_location_name: str = ""
    primary_address_heuristic: bool = True

    replay_fixture: str = ""
    # Where the incremental baseline is kept. Defaults to the directory
    # infrahub-sync already uses for its own run cache.
    cache_directory: str = ".infrahub-sync-cache"

    @field_validator(
        "url",
        "username",
        "password",
        "network_id",
        "snapshot_id",
        "replay_fixture",
        "cache_directory",
        mode="before",
    )
    @classmethod
    def _expand_env(cls, value: Any) -> Any:
        return _expand(value)

    @field_validator("device_vendors")
    @classmethod
    def _known_vendors(cls, value: list[str]) -> list[str]:
        return canonical_enum_values(value, VENDOR, "device_vendors")

    @field_validator("device_types")
    @classmethod
    def _known_device_types(cls, value: list[str]) -> list[str]:
        return canonical_enum_values(value, DEVICE_TYPE, "device_types")

    @field_validator("query_directory")
    @classmethod
    def _directory_shape(cls, value: str) -> str:
        text = value.strip()
        if not text.startswith("/"):
            text = "/" + text
        return text.rstrip("/") or "/"

    @property
    def device_scope_active(self) -> bool:
        return bool(self.device_vendors or self.device_types or self.device_models)

    @property
    def cloud_scope_active(self) -> bool:
        return bool(self.cloud_types or self.cloud_account_ids)

    @property
    def filtered_scope(self) -> bool:
        return self.device_scope_active or self.cloud_scope_active

    @property
    def user_agent(self) -> str:
        return f"forward-infrahub/{__version__}"

    def resolved_network_id(self) -> str:
        network_id = self.network_id or os.environ.get(ENV_NETWORK_ID, "")
        if not network_id:
            raise ForwardConfigurationError(
                f"network_id is required: set source.settings.network_id or {ENV_NETWORK_ID}"
            )
        return str(network_id).strip()


def build_client(settings: AdapterSettings) -> Any:
    """Return the Forward client the adapter will use.

    A ``replay_fixture`` yields an offline client that serves rows from a JSON
    file; otherwise this is a ``forward_sdk.ForwardClient`` configured from the
    settings, with ``FORWARD_*`` environment variables filling in whatever the
    settings leave unset.
    """
    if settings.replay_fixture:
        return ReplayClient.from_file(settings.replay_fixture)

    url = settings.url or os.environ.get(ENV_URL)
    if not url:
        raise ForwardConfigurationError(f"url is required: set source.settings.url or {ENV_URL}")

    verify: bool | str = settings.verify_ssl
    if settings.verify_ssl is True and os.environ.get(ENV_VERIFY_TLS, "").lower() in {
        "0",
        "false",
        "no",
    }:
        verify = False

    return ForwardClient(
        url,
        username=settings.username or os.environ.get(ENV_USERNAME),
        password=settings.password or os.environ.get(ENV_PASSWORD),
        verify=verify,
        timeout=settings.timeout,
        retries=settings.retries,
        network_id=settings.resolved_network_id(),
        user_agent=settings.user_agent,
    )

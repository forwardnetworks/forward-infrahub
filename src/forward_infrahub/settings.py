"""Adapter settings, validated from the ``source.settings`` block of ``config.yml``."""

from __future__ import annotations

import os
from typing import Any, Literal

from forward_sdk.nqe.enums import suggest
from pydantic import BaseModel, ConfigDict, Field, field_validator

from forward_infrahub import __version__
from forward_infrahub.exceptions import InvalidConfiguration
from forward_infrahub.nqe_types import DEVICE_TYPE, MEMBER_SOURCES, VENDOR, nqe_members
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
        # Empty means the data model does not describe this type, rather than
        # describing it as having no valid members: every one of the 59 types it
        # carries has at least one, measured by the forward-sdk maintainers. So
        # this is an absence of information, not a statement that nothing is
        # valid, and refusing every value here would turn a gap in the data
        # model into a hard failure.
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
    # Extra submissions of a query Forward ran and finished with a transient
    # outcome (``SYSTEM_ERROR``). ``retries`` covers failed HTTP requests; this
    # covers a query that ran and ended badly. 0 turns it off.
    outcome_retries: int = Field(default=2, ge=0)
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

    # Snapshot-to-snapshot diffs are the normal fetch; a full fetch happens on
    # the first run, when the baseline is invalid, and every
    # ``full_refetch_every`` runs so drift in the cached rows cannot persist.
    # 0 means never on cadence.
    incremental: bool = True
    full_refetch_every: int = Field(default=20, ge=0)
    # A full fetch that could not be paged to completion -- the row-count or
    # page-count guard, or a server that genuinely never advances the offset --
    # is re-run in shards of this many devices.
    shard_devices: int = Field(default=100, ge=1)
    require_complete_snapshot: bool = False

    normalize_location_names: bool = True
    unassigned_location_name: str = ""

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
            raise InvalidConfiguration(
                f"network_id is required: set source.settings.network_id or {ENV_NETWORK_ID}"
            )
        return str(network_id).strip()

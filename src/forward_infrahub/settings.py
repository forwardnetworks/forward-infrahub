"""Adapter settings, validated from the ``source.settings`` block of ``config.yml``."""

from __future__ import annotations

import os
from typing import Any, Literal

from forward_sdk import ForwardClient, ForwardConfigurationError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from forward_infrahub import __version__
from forward_infrahub.replay import ReplayClient

LATEST_PROCESSED = "latestProcessed"
LATEST_COLLECTED = "latestCollected"

ENV_URL = "FORWARD_URL"
ENV_USERNAME = "FORWARD_USERNAME"
ENV_PASSWORD = "FORWARD_PASSWORD"
ENV_NETWORK_ID = "FORWARD_NETWORK_ID"
ENV_VERIFY_TLS = "FORWARD_VERIFY_TLS"

QueryMode = Literal["auto", "published", "inline"]


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

    @field_validator(
        "url", "username", "password", "network_id", "snapshot_id", "replay_fixture", mode="before"
    )
    @classmethod
    def _expand_env(cls, value: Any) -> Any:
        return _expand(value)

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

"""Forward, through its Python SDK: the Forward port, and building the SDK's client.

``ForwardSdkAdapter`` implements the Forward port over any object shaped like the
SDK's client: the real ``forward_sdk.ForwardClient``, or the offline replay client.
``connect`` builds the real one from the settings, with the ``FORWARD_*``
environment variables filling in whatever the settings leave unset. Nothing else
in the package touches the SDK's client.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from forward_sdk import ForwardClient, ForwardConfigurationError
from forward_sdk.nqe import QueryRef

from forward_infrahub.ports import Row
from forward_infrahub.settings import (
    ENV_PASSWORD,
    ENV_URL,
    ENV_USERNAME,
    ENV_VERIFY_TLS,
    AdapterSettings,
)


class ForwardSdkAdapter:
    """The Forward port over an SDK-shaped client."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def run_query(self, ref: QueryRef, **options: Any) -> list[Row]:
        return list(self.client.nqe.query(ref, **options))

    def diff_query(self, ref: QueryRef, *, before: str, after: str, **options: Any) -> list[Any]:
        return list(self.client.nqe.diff(ref, before=before, after=after, **options))

    def resolve_query(self, ref: QueryRef) -> QueryRef:
        resolved: QueryRef = self.client.nqe.resolve(ref)
        return resolved

    def library_source(self, path: str) -> str:
        return str(self.client.nqe.repo.source(path))

    def publish_query(self, files: Mapping[str, str], **options: Any) -> Any:
        return self.client.nqe.repo.publish(files, **options)

    def latest_processed_snapshot(self, network_id: str) -> str | None:
        snapshot: str | None = self.client.snapshots.latest_processed_id(network_id)
        return snapshot

    def latest_collected_snapshot(self, network_id: str, **options: Any) -> str:
        snapshot: str = self.client.snapshots.latest_collected_id(network_id, **options)
        return snapshot

    def snapshot_metrics(self, snapshot_id: str) -> Any:
        return self.client.snapshots.metrics(snapshot_id)


def connect(settings: AdapterSettings) -> ForwardClient:
    """The SDK's client for the settings, the environment filling what they leave unset."""
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

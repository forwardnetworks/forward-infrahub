"""Forward, through its Python SDK: the Forward port, and building the SDK's client.

``ForwardSdkAdapter`` implements the Forward port over any object shaped like the
SDK's client: the real ``forward_sdk.ForwardClient``, or the offline replay client.
``connect`` builds the real one from the settings, with the ``FORWARD_*``
environment variables filling in whatever the settings leave unset. Nothing else
in the package touches the SDK's client.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from forward_sdk import ForwardClient, ForwardError, ForwardNotFoundError, ForwardPaginationError
from forward_sdk.nqe import QueryRef

from forward_infrahub.ports import (
    ForwardCallFailed,
    ForwardCallNotFound,
    ForwardCallPagingFailed,
    InvalidConfiguration,
    Row,
)
from forward_infrahub.settings import (
    ENV_PASSWORD,
    ENV_URL,
    ENV_USERNAME,
    ENV_VERIFY_TLS,
    AdapterSettings,
)


@contextmanager
def translating() -> Iterator[None]:
    """Raise the package's error for any error the SDK raises inside the block.

    The message is the SDK error's own and the SDK error is the cause. Only the two failures a
    caller acts on get their own class; everything else is a generic call failure.
    """
    try:
        yield
    except ForwardError as exc:
        if isinstance(exc, ForwardNotFoundError):
            raise ForwardCallNotFound(str(exc)) from exc
        if isinstance(exc, ForwardPaginationError):
            raise ForwardCallPagingFailed(str(exc)) from exc
        raise ForwardCallFailed(str(exc)) from exc


class ForwardSdkAdapter:
    """The Forward port over an SDK-shaped client; the SDK's errors become the package's."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def run_query(self, ref: QueryRef, **options: Any) -> list[Row]:
        with translating():
            return list(self.client.nqe.query(ref, **options))

    def diff_query(self, ref: QueryRef, *, before: str, after: str, **options: Any) -> list[Any]:
        with translating():
            return list(self.client.nqe.diff(ref, before=before, after=after, **options))

    def resolve_query(self, ref: QueryRef) -> QueryRef:
        with translating():
            resolved: QueryRef = self.client.nqe.resolve(ref)
        return resolved

    def library_source(self, path: str) -> str:
        with translating():
            return str(self.client.nqe.repo.source(path))

    def publish_query(self, files: Mapping[str, str], **options: Any) -> Any:
        with translating():
            return self.client.nqe.repo.publish(files, **options)

    def latest_processed_snapshot(self, network_id: str) -> str | None:
        with translating():
            snapshot: str | None = self.client.snapshots.latest_processed_id(network_id)
        return snapshot

    def latest_collected_snapshot(self, network_id: str, **options: Any) -> str:
        with translating():
            snapshot: str = self.client.snapshots.latest_collected_id(network_id, **options)
        return snapshot

    def snapshot_metrics(self, snapshot_id: str) -> Any:
        with translating():
            return self.client.snapshots.metrics(snapshot_id)


def connect(settings: AdapterSettings) -> ForwardClient:
    """The SDK's client for the settings, the environment filling what they leave unset."""
    url = settings.url or os.environ.get(ENV_URL)
    if not url:
        raise InvalidConfiguration(f"url is required: set source.settings.url or {ENV_URL}")

    verify: bool | str = settings.verify_ssl
    if settings.verify_ssl is True and os.environ.get(ENV_VERIFY_TLS, "").lower() in {
        "0",
        "false",
        "no",
    }:
        verify = False

    with translating():
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

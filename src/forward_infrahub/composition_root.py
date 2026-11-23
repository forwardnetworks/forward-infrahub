"""Where the package's collaborators are built.

The plugin and the command line ask here for what they need: a Forward client for
the settings, the Forward port over a client, the row store for a sync, and the
query catalogue. infrahub-sync constructs the plugin itself, so it cannot be handed
its collaborators; it calls these instead. Nothing here imports the plugin or the
command line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forward_infrahub.forward_client import ForwardSdkAdapter, connect
from forward_infrahub.ports import ForwardPort, QueryCataloguePort, RowStorePort
from forward_infrahub.queries import BundledQueries
from forward_infrahub.replay import ReplayClient
from forward_infrahub.rowcache import RowCache
from forward_infrahub.settings import AdapterSettings


def build_client(settings: AdapterSettings) -> Any:
    """The Forward client a run uses.

    A ``replay_fixture`` yields an offline client that serves rows from a JSON file;
    otherwise this is the SDK's client, configured from the settings and the
    ``FORWARD_*`` environment variables.
    """
    if settings.replay_fixture:
        return ReplayClient.from_file(settings.replay_fixture)
    return connect(settings)


def forward_port(client: Any) -> ForwardPort:
    """The Forward port over a client built by ``build_client`` (or put in its place)."""
    if isinstance(client, ReplayClient) and client.catalogue is None:
        # The replay client recognises an inline query by its source, so it needs
        # the catalogue; one built outside this module gets it here.
        client.catalogue = query_catalogue()
    return ForwardSdkAdapter(client)


def row_store(directory: str | Path, sync_name: str) -> RowStorePort:
    """The rows and baseline of one sync's last clean run."""
    return RowCache(directory, sync_name)


def query_catalogue() -> QueryCataloguePort:
    """The query sources: the bundled queries, and files a mapping names."""
    return BundledQueries()

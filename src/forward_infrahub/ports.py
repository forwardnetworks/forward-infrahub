"""The ports: what the use cases need from outside, and what the adapters see of the core.

Three ports, each a ``Protocol`` an adapter implements:
Forward, the row store that keeps the rows of the last clean run, and the catalogue
of query sources. The rest of this module re-exports the domain names the adapters
use, so an adapter imports the core through here and through nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from forward_sdk.nqe import QueryRef

from forward_infrahub import __version__
from forward_infrahub.contracts import (
    QUERY_FILENAMES,
    QueryContractReader,
    check_contracts,
    library_path,
    primary_key_columns,
    query_stem,
)
from forward_infrahub.exceptions import (
    ForwardCallFailed,
    ForwardCallNotFound,
    ForwardCallPagingFailed,
    ForwardInfrahubError,
    ForwardLoadError,
    ForwardSafetyError,
    InvalidConfiguration,
)
from forward_infrahub.filters import CUSTOM_FILTERS
from forward_infrahub.identity import check_identities
from forward_infrahub.rowdiff import (
    Baseline,
    ResolvedQuery,
    Row,
    apply_diff,
    full_fetch_reason,
)
from forward_infrahub.scope import ForwardCloudScope, ForwardDeviceScope, build_scope_fingerprint
from forward_infrahub.tables import (
    CLOUD_SCOPE_FILE,
    DEVICE_SCOPE_FILE,
    GENERIC_PEERS,
    AugmentContext,
    TableRef,
    parse_mapping,
)

__all__ = [
    "CLOUD_SCOPE_FILE",
    "CUSTOM_FILTERS",
    "DEVICE_SCOPE_FILE",
    "GENERIC_PEERS",
    "QUERY_FILENAMES",
    "AugmentContext",
    "Baseline",
    "ForwardCallFailed",
    "ForwardCallNotFound",
    "ForwardCallPagingFailed",
    "ForwardCloudScope",
    "ForwardDeviceScope",
    "ForwardInfrahubError",
    "ForwardLoadError",
    "ForwardPort",
    "ForwardSafetyError",
    "InvalidConfiguration",
    "QueryCataloguePort",
    "QueryContractReader",
    "ResolvedQuery",
    "Row",
    "RowStorePort",
    "TableRef",
    "__version__",
    "apply_diff",
    "build_scope_fingerprint",
    "check_contracts",
    "check_identities",
    "full_fetch_reason",
    "library_path",
    "parse_mapping",
    "primary_key_columns",
    "query_stem",
]


class ForwardPort(Protocol):
    """Forward, as the package uses it: the eight operations, and no others."""

    def run_query(self, ref: QueryRef, **options: Any) -> list[Row]:
        """Execute a query on a network's snapshot and return every row."""
        ...

    def diff_query(self, ref: QueryRef, *, before: str, after: str, **options: Any) -> list[Any]:
        """The diff entries of a committed query between two snapshots."""
        ...

    def resolve_query(self, ref: QueryRef) -> QueryRef:
        """Resolve a library path or id to a runnable, pinned reference."""
        ...

    def library_source(self, path: str) -> str:
        """The committed source of a query in the library."""
        ...

    def publish_query(self, files: Mapping[str, str], **options: Any) -> Any:
        """Stage, validate and commit query sources to the library in one commit."""
        ...

    def latest_processed_snapshot(self, network_id: str) -> str | None:
        """The newest processed snapshot of a network, by Forward's own rule."""
        ...

    def latest_collected_snapshot(self, network_id: str, **options: Any) -> str:
        """The newest snapshot that collected the devices a probe names."""
        ...

    def snapshot_metrics(self, snapshot_id: str) -> Any:
        """A snapshot's collection and processing metrics."""
        ...


class RowStorePort(Protocol):
    """The rows and baseline of the last clean run, kept between runs."""

    def rows(self, key: str) -> list[Row] | None:
        """The cached rows of one query's table, or ``None`` when there are none."""
        ...

    def baseline(self) -> Baseline | None:
        """The baseline the cached rows belong to, or ``None`` when there is none."""
        ...

    def commit(self, baseline: Baseline, rows_by_key: Mapping[str, Sequence[Row]]) -> None:
        """Write the rows of every table and their baseline, together."""
        ...


class QueryCataloguePort(Protocol):
    """Where query sources come from: the bundled queries, or a file a mapping names."""

    def bundled_source(self, filename: str) -> str:
        """The source of a bundled query, exactly as shipped."""
        ...

    def file_source(self, path: str) -> str:
        """The source of a query file a mapping names, ready to run."""
        ...

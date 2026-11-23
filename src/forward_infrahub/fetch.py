"""Running the bundled queries against Forward and caching their rows."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from forward_sdk import ForwardConfigurationError
from forward_sdk.nqe import PageGuards, QueryRef, where
from forward_sdk.nqe.files import strip_primary_key

from forward_infrahub.ports import (
    ForwardCallFailed,
    ForwardCallNotFound,
    ForwardCallPagingFailed,
    ForwardLoadError,
    ForwardPort,
    ForwardSafetyError,
    InvalidConfiguration,
    QueryCataloguePort,
    ResolvedQuery,
    Row,
    TableRef,
    library_path,
    primary_key_columns,
)
from forward_infrahub.settings import AdapterSettings

__all__ = [
    "QueryRunner",
    "ResolvedQuery",
    "merge_shards",
    "resolve_queries",
    "shard_source",
]

logger = logging.getLogger(__name__)

#: The exact comprehension line every shardable bundled query opens with. A
#: shard appends a ``where`` on the device name right after it.
DEVICE_LOOP = "foreach device in network.devices"


def _ref_for(
    key: str, settings: AdapterSettings, catalogue: QueryCataloguePort
) -> tuple[QueryRef, str]:
    """The unresolved reference for an execution key, plus a label for messages."""
    scheme, _, value = key.partition(":")
    try:
        if scheme == "file":
            return QueryRef.by_path(library_path(settings.query_directory, value)), value
        if scheme == "nqe-path":
            return QueryRef.by_path(value), value
        if scheme == "nqe-id":
            query_id, _, commit = value.partition("@")
            by_id = QueryRef.by_id(query_id, commit_id=commit or None)
            # The SDK checks the commit only when it is read; read it now, so an abbreviated
            # hash is refused here with the rest of the reference errors.
            _ = by_id.effective_commit_id
            return by_id, value
        if scheme == "nqe-file":
            return QueryRef.inline(catalogue.file_source(value)), value
    except ForwardConfigurationError as exc:
        # The SDK refuses a reference it cannot use (an empty path, an abbreviated commit).
        raise InvalidConfiguration(str(exc)) from exc
    raise ForwardLoadError(f"cannot build a query reference for {key!r}")


def _commit_of(ref: QueryRef) -> str:
    """The commit a reference is pinned to, or empty when it is not pinned.

    The SDK drops ``head`` and refuses an abbreviated hash; the latter is a configuration error.
    """
    try:
        return ref.effective_commit_id or ""
    except ForwardConfigurationError as exc:
        raise InvalidConfiguration(str(exc)) from exc


def resolve_queries(
    forward: ForwardPort,
    keys: Iterable[str],
    settings: AdapterSettings,
    catalogue: QueryCataloguePort,
) -> dict[str, ResolvedQuery[QueryRef]]:
    """Resolve every execution key to a runnable query reference.

    A bundled query is preferred by library path, because a published query can
    be diffed between snapshots and an inline one cannot. When it has not been
    published yet, ``query_mode: auto`` falls back to running the bundled source
    inline and says so; ``published`` refuses instead.
    """
    resolved: dict[str, ResolvedQuery[QueryRef]] = {}
    for key in keys:
        ref, label = _ref_for(key, settings, catalogue)
        if ref.mode == "text":
            resolved[key] = ResolvedQuery(key, ref, False, label, _commit_of(ref))
            continue
        try:
            runnable = forward.resolve_query(ref)
            resolved[key] = ResolvedQuery(key, runnable, True, label, _commit_of(runnable))
        except ForwardCallNotFound:
            if settings.query_mode == "published" or not key.startswith("file:"):
                raise ForwardSafetyError(
                    f"query {label!r} is not in the Forward library under "
                    f"{settings.query_directory!r}. Publish it with "
                    "`forward-infrahub publish-queries`, or set query_mode: inline."
                ) from None
            filename = key.split(":", 1)[1]
            logger.warning(
                "query %s is not published under %s; running it inline. Snapshot diffs are "
                "disabled for it until `forward-infrahub publish-queries` has run.",
                filename,
                settings.query_directory,
            )
            inline = QueryRef.inline(strip_primary_key(catalogue.bundled_source(filename)))
            resolved[key] = ResolvedQuery(key, inline, False, label, _commit_of(inline))
    return resolved


def shard_source(source: str, device_names: Sequence[str]) -> str:
    """The execution source narrowed to ``device_names`` at every device loop.

    Raises:
        ForwardLoadError: the source has no device loop to narrow.
    """
    clause = where.one_of("device.name", list(device_names))
    if DEVICE_LOOP not in source or clause is None:
        raise ForwardLoadError("query has no device comprehension to shard on")
    return source.replace(DEVICE_LOOP, f"{DEVICE_LOOP}\nwhere {clause}")


def merge_shards(shards: Iterable[Sequence[Row]], primary_key: Sequence[str]) -> list[Row]:
    """Concatenate shard results, folding rows that share a primary key.

    A table aggregated over devices (a manufacturer, a VLAN) appears once per
    shard that saw it; those rows are one row with their list columns united.
    """
    merged: dict[tuple[str, ...], Row] = {}
    for rows in shards:
        for row in rows:
            key = tuple(str(row.get(c) if row.get(c) is not None else "") for c in primary_key)
            existing = merged.get(key)
            if existing is None:
                merged[key] = dict(row)
                continue
            for column, value in row.items():
                if isinstance(value, list) and isinstance(existing.get(column), list):
                    seen = set(map(str, existing[column]))
                    existing[column] = existing[column] + [v for v in value if str(v) not in seen]
    return list(merged.values())


class QueryRunner:
    """Runs queries with a small pool and hands back their rows.

    Concurrency is deliberately low. Forward's asynchronous execution endpoint
    becomes unreliable when one client submits several expensive queries at
    once, and two workers already overlap fetch with processing.
    """

    def __init__(
        self,
        forward: ForwardPort,
        settings: AdapterSettings,
        snapshot_id: str,
        *,
        catalogue: QueryCataloguePort | None = None,
    ) -> None:
        self._forward = forward
        #: The bundled sources a sharded fetch narrows; without it, nothing shards.
        self._catalogue = catalogue
        self._settings = settings
        self._snapshot_id = snapshot_id
        self._lock = threading.Lock()
        self._rows: dict[str, list[Row]] = {}
        #: Every device name in the snapshot, the universe a sharded fetch
        #: partitions. Set by the adapter once the devices table is in.
        self.device_names: list[str] = []
        #: Execution keys that were fetched in shards, with the shard count.
        self.sharded: dict[str, int] = {}

    @property
    def guards(self) -> PageGuards:
        return (
            PageGuards(max_rows=self._settings.max_rows)
            if self._settings.max_rows
            else PageGuards()
        )

    def rows_for(self, key: str) -> list[Row]:
        with self._lock:
            return self._rows[key]

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._rows

    def seed(self, key: str, rows: Sequence[Row]) -> None:
        """Hand the runner rows it did not fetch: from the cache, or a diff."""
        with self._lock:
            self._rows[key] = [dict(row) for row in rows]

    def cached(self) -> Mapping[str, list[Row]]:
        with self._lock:
            return dict(self._rows)

    def diff(self, query: ResolvedQuery[QueryRef], before: str, after: str) -> list[Any]:
        """The diff entries of one published query between two snapshots."""
        return list(
            self._forward.diff_query(
                query.ref,
                before=before,
                after=after,
                page_size=self._settings.page_size,
                guards=self.guards,
            )
        )

    def run_one(self, query: ResolvedQuery[QueryRef], network_id: str) -> list[Row]:
        try:
            rows = self._fetch(query.ref, network_id, label=query.label)
        except ForwardCallPagingFailed:
            rows = self._run_sharded(query, network_id)
        except ForwardCallFailed as exc:
            raise ForwardLoadError(f"query {query.label!r} failed: {exc}") from exc
        with self._lock:
            self._rows[query.key] = rows
        logger.info("fetched %d row(s) from %s", len(rows), query.label)
        return rows

    def _fetch(self, ref: QueryRef, network_id: str, *, label: str) -> list[Row]:
        # The SDK runs, waits and pages; with ``outcome_retries`` it also
        # resubmits a query Forward finished with a transient outcome
        # (``SYSTEM_ERROR``). A timeout, a compile error and a paging stall are
        # not retried here: a stall falls through to sharding in ``run_one``.
        return list(
            self._forward.run_query(
                ref,
                network_id=network_id,
                snapshot_id=self._snapshot_id,
                page_size=self._settings.page_size,
                guards=self.guards,
                timeout=self._settings.timeout,
                outcome_retries=self._settings.outcome_retries,
            )
        )

    def _run_sharded(self, query: ResolvedQuery[QueryRef], network_id: str) -> list[Row]:
        """Re-run a bundled query in device shards small enough to page.

        The fallback for a query whose full-network execution could not be
        paged to completion: the row-count guard (``settings.max_rows``,
        default a generous 2,000,000), the page-count guard, or a server that
        genuinely never advances the offset. Splitting by device shrinks each
        execution's result until it fits inside those same guards.
        """
        filename = query.filename
        if filename is None:
            raise ForwardLoadError(
                f"query {query.label!r} could not be paged to completion, and only "
                "bundled queries can be sharded; narrow it in the library"
            )
        if not self.device_names:
            raise ForwardLoadError(
                f"query {query.label!r} could not be paged to completion and no device "
                "list is available to shard it"
            )
        if self._catalogue is None:
            raise ForwardLoadError(
                f"query {query.label!r} could not be paged to completion and no query "
                "catalogue is available to shard it"
            )
        bundled = self._catalogue.bundled_source(filename)
        source = strip_primary_key(bundled)
        primary_key = primary_key_columns(bundled)
        size = self._settings.shard_devices
        shards = [self.device_names[i : i + size] for i in range(0, len(self.device_names), size)]
        logger.warning(
            "%s could not be paged to completion; fetching it in %d shard(s) of up to %d devices",
            query.label,
            len(shards),
            size,
        )
        results = [self._fetch_shard(source, names, network_id, query.label) for names in shards]
        self.sharded[query.key] = len(results)
        return merge_shards(results, primary_key)

    def _fetch_shard(
        self, source: str, names: Sequence[str], network_id: str, label: str
    ) -> list[Row]:
        try:
            shard = QueryRef.inline(shard_source(source, names))
        except ForwardLoadError as exc:
            # A source with no device loop cannot be sharded; say which query, as before.
            raise ForwardLoadError(f"query {label!r} failed: {exc}") from exc
        try:
            rows = self._fetch(shard, network_id, label=label)
        except ForwardCallPagingFailed:
            if len(names) == 1:
                raise ForwardLoadError(
                    f"query {label!r} could not be paged to completion even for one "
                    f"device, {names[0]}"
                ) from None
            half = len(names) // 2
            return self._fetch_shard(source, names[:half], network_id, label) + self._fetch_shard(
                source, names[half:], network_id, label
            )
        except ForwardCallFailed as exc:
            raise ForwardLoadError(f"query {label!r} failed: {exc}") from exc
        logger.info("fetched %d row(s) from %s for %d device(s)", len(rows), label, len(names))
        return rows

    def run_all(self, queries: Iterable[ResolvedQuery[QueryRef]], network_id: str) -> None:
        pending = [q for q in queries if not self.has(q.key)]
        if not pending:
            return
        workers = min(self._settings.max_concurrent_queries, len(pending))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self.run_one, query, network_id) for query in pending]
            for future in futures:
                future.result()

    def rows_for_ref(self, ref: TableRef) -> list[Row]:
        """Every row backing a table, concatenated in query order."""
        rows: list[Row] = []
        for key in ref.execution_keys:
            rows.extend(self.rows_for(key))
        return rows

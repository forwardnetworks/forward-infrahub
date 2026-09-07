"""Running the bundled queries against Forward and caching their rows."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forward_sdk import ForwardError, ForwardNotFoundError
from forward_sdk.nqe import PageGuards, QueryRef
from forward_sdk.nqe.files import load_query

from forward_infrahub.exceptions import ForwardLoadError, ForwardSafetyError
from forward_infrahub.queries import library_path, read_query_execution_source
from forward_infrahub.settings import AdapterSettings
from forward_infrahub.tables import TableRef

logger = logging.getLogger(__name__)

Row = dict[str, Any]


@dataclass(frozen=True)
class ResolvedQuery:
    """One query the run will execute, and whether it can be diffed."""

    key: str
    ref: QueryRef
    diff_capable: bool
    label: str

    @property
    def commit_id(self) -> str:
        return self.ref.effective_commit_id or ""


def _ref_for(key: str, settings: AdapterSettings) -> tuple[QueryRef, str]:
    """The unresolved reference for an execution key, plus a label for messages."""
    scheme, _, value = key.partition(":")
    if scheme == "file":
        return QueryRef.by_path(library_path(settings.query_directory, value)), value
    if scheme == "nqe-path":
        return QueryRef.by_path(value), value
    if scheme == "nqe-id":
        query_id, _, commit = value.partition("@")
        return QueryRef.by_id(query_id, commit_id=commit or None), value
    if scheme == "nqe-file":
        return QueryRef.inline(load_query(Path(value))), value
    raise ForwardLoadError(f"cannot build a query reference for {key!r}")


def resolve_queries(
    client: Any, keys: Iterable[str], settings: AdapterSettings
) -> dict[str, ResolvedQuery]:
    """Resolve every execution key to a runnable query reference.

    A bundled query is preferred by library path, because a published query can
    be diffed between snapshots and an inline one cannot. When it has not been
    published yet, ``query_mode: auto`` falls back to running the bundled source
    inline and says so; ``published`` refuses instead.
    """
    resolved: dict[str, ResolvedQuery] = {}
    for key in keys:
        ref, label = _ref_for(key, settings)
        if ref.mode == "text":
            resolved[key] = ResolvedQuery(key, ref, diff_capable=False, label=label)
            continue
        try:
            runnable = client.nqe.resolve(ref)
            resolved[key] = ResolvedQuery(key, runnable, diff_capable=True, label=label)
        except ForwardNotFoundError:
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
            inline = QueryRef.inline(read_query_execution_source(filename))
            resolved[key] = ResolvedQuery(key, inline, diff_capable=False, label=label)
    return resolved


class QueryRunner:
    """Runs queries with a small pool and hands back their rows.

    Concurrency is deliberately low. Forward's asynchronous execution endpoint
    becomes unreliable when one client submits several expensive queries at
    once, and two workers already overlap fetch with processing.
    """

    def __init__(self, client: Any, settings: AdapterSettings, snapshot_id: str) -> None:
        self._client = client
        self._settings = settings
        self._snapshot_id = snapshot_id
        self._lock = threading.Lock()
        self._rows: dict[str, list[Row]] = {}

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

    def cached(self) -> Mapping[str, list[Row]]:
        with self._lock:
            return dict(self._rows)

    def run_one(self, query: ResolvedQuery, network_id: str) -> list[Row]:
        try:
            execution = self._client.nqe.execute(
                query.ref, network_id=network_id, snapshot_id=self._snapshot_id
            )
            execution.wait(timeout=self._settings.timeout)
            rows = list(execution.rows(page_size=self._settings.page_size, guards=self.guards))
        except ForwardError as exc:
            raise ForwardLoadError(f"query {query.label!r} failed: {exc}") from exc
        with self._lock:
            self._rows[query.key] = rows
        logger.info("fetched %d row(s) from %s", len(rows), query.label)
        return rows

    def run_all(self, queries: Iterable[ResolvedQuery], network_id: str) -> None:
        pending = [q for q in queries if q.key not in self.cached()]
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

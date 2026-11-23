"""Publishing the bundled queries to the Forward query library, and auditing them.

A published query can be diffed between two snapshots; an inline one cannot,
because Forward has no committed version to compare against. Publishing is
never part of a sync: it changes shared state in Forward, so it is an explicit
command an operator runs.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from forward_infrahub.ports import (
    QUERY_FILENAMES,
    ForwardCallFailed,
    ForwardCallNotFound,
    ForwardPort,
    QueryCataloguePort,
    library_path,
)

__all__ = [
    "MATCHED",
    "MISSING",
    "STALE",
    "UNAVAILABLE",
    "audit",
    "normalize_source",
    "publish",
]

logger = logging.getLogger(__name__)

MATCHED = "matched"
STALE = "stale"
MISSING = "missing"
UNAVAILABLE = "source-unavailable"


def normalize_source(source: str) -> str:
    """Compare query text without being fooled by trailing whitespace."""
    return "\n".join(line.rstrip() for line in source.splitlines()).strip()


@dataclass(frozen=True)
class AuditEntry:
    filename: str
    path: str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == MATCHED


def audit(
    forward: ForwardPort,
    directory: str,
    filenames: Iterable[str] = QUERY_FILENAMES,
    *,
    catalogue: QueryCataloguePort,
) -> list[AuditEntry]:
    """Compare what is committed in the library against what this package ships.

    Compares the committed source itself rather than trusting that a query
    exists at the path: a query can be present, and be an older version that
    produces different columns.
    """
    entries: list[AuditEntry] = []
    for filename in filenames:
        path = library_path(directory, filename)
        try:
            committed = forward.library_source(path)
        except ForwardCallNotFound:
            # Nothing at that path, or nothing readable there. Either way the
            # answer for an operator is the same: publish it.
            entries.append(AuditEntry(filename, path, MISSING))
            continue
        except ForwardCallFailed as exc:
            # We could not ask, which is different from having asked and
            # found nothing.
            entries.append(AuditEntry(filename, path, UNAVAILABLE, str(exc)))
            continue
        if normalize_source(committed) == normalize_source(catalogue.bundled_source(filename)):
            entries.append(AuditEntry(filename, path, MATCHED))
        else:
            entries.append(AuditEntry(filename, path, STALE, "committed source differs"))
    return entries


def publish(
    forward: ForwardPort,
    directory: str,
    *,
    catalogue: QueryCataloguePort,
    snapshot_id: str | None = None,
    filenames: Iterable[str] = QUERY_FILENAMES,
    title: str = "Publish forward-infrahub queries",
) -> Any:
    """Stage, validate and commit the bundled queries in one commit.

    Validation runs against a real snapshot, so a query that no longer compiles
    is caught here rather than by whoever next opens the library.
    """
    files = {library_path(directory, name): catalogue.bundled_source(name) for name in filenames}
    return forward.publish_query(
        files,
        title=title,
        body="Queries bundled with forward-infrahub.",
        dry_run_snapshot_id=snapshot_id,
    )

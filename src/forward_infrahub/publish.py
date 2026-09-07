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

from forward_sdk import ForwardError, ForwardNotFoundError

from forward_infrahub.queries import (
    QUERY_FILENAMES,
    library_path,
    read_query_source,
)

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
    client: Any, directory: str, filenames: Iterable[str] = QUERY_FILENAMES
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
            found = client.nqe.repo.queries(path=path, with_source=True)
        except ForwardNotFoundError:
            entries.append(AuditEntry(filename, path, MISSING))
            continue
        except ForwardError as exc:
            entries.append(AuditEntry(filename, path, UNAVAILABLE, str(exc)))
            continue
        match = next((q for q in found if getattr(q, "path", None) == path), None)
        if match is None:
            entries.append(AuditEntry(filename, path, MISSING))
            continue
        # Populated only because the lookup asked for it; None otherwise.
        committed = match.source
        if not committed:
            entries.append(AuditEntry(filename, path, UNAVAILABLE, "no source returned"))
            continue
        if normalize_source(committed) == normalize_source(read_query_source(filename)):
            entries.append(AuditEntry(filename, path, MATCHED))
        else:
            entries.append(AuditEntry(filename, path, STALE, "committed source differs"))
    return entries


def publish(
    client: Any,
    directory: str,
    *,
    snapshot_id: str | None = None,
    filenames: Iterable[str] = QUERY_FILENAMES,
    title: str = "Publish forward-infrahub queries",
) -> Any:
    """Stage, validate and commit the bundled queries in one commit.

    Validation runs against a real snapshot, so a query that no longer compiles
    is caught here rather than by whoever next opens the library.
    """
    files = {library_path(directory, name): read_query_source(name) for name in filenames}
    return client.nqe.repo.publish(
        files,
        title=title,
        body="Queries bundled with forward-infrahub.",
        dry_run_snapshot_id=snapshot_id,
    )

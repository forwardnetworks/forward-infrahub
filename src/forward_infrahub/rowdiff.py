"""The arithmetic of incremental runs: rows, their keys, a baseline, and a diff applied.

A run reads a snapshot diff only when the rows of the last clean run are at hand
and still describe the same queries and scope; ``full_fetch_reason`` says when
they do not. ``apply_diff`` folds Forward's diff entries into those rows. Nothing
here reads or writes anything: the file store that keeps the rows between runs is
the row cache adapter.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from forward_sdk.nqe import QueryRef

__all__ = [
    "Baseline",
    "ResolvedQuery",
    "Row",
    "apply_diff",
    "full_fetch_reason",
]

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

    @property
    def filename(self) -> str | None:
        """The bundled file behind a ``file:`` key, which is what can be sharded."""
        scheme, _, value = self.key.partition(":")
        return value if scheme == "file" else None


@dataclass(frozen=True)
class Baseline:
    """What the previous clean run loaded, so a diff knows what it is diffing from."""

    snapshot_id: str
    scope_fingerprint: str
    query_commits: dict[str, str] = field(default_factory=dict)
    loaded_at: str = ""
    runs_since_full: int = 0
    #: Execution key to the number of rows cached for it.
    tables: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Any) -> Baseline | None:
        if not isinstance(payload, dict) or not payload.get("snapshot_id"):
            return None
        return cls(
            snapshot_id=str(payload["snapshot_id"]),
            scope_fingerprint=str(payload.get("scope_fingerprint") or ""),
            query_commits={str(k): str(v) for k, v in (payload.get("query_commits") or {}).items()},
            loaded_at=str(payload.get("loaded_at") or ""),
            runs_since_full=int(payload.get("runs_since_full") or 0),
            tables={str(k): int(v) for k, v in (payload.get("tables") or {}).items()},
        )


@dataclass(frozen=True)
class DiffOutcome:
    rows: list[Row]
    added: int
    modified: int
    deleted: int

    @property
    def summary(self) -> str:
        return f"+{self.added} ~{self.modified} -{self.deleted}"


def full_fetch_reason(
    *,
    queries: Iterable[ResolvedQuery],
    baseline: Baseline | None,
    fingerprint: str,
    incremental: bool,
    full_refetch_every: int,
) -> str:
    """Why this run must fetch everything, or "" when it may apply diffs."""
    if not incremental:
        return "incremental is off"
    queries = list(queries)
    inline = sorted(q.label for q in queries if not q.diff_capable)
    if inline:
        return f"queries not published: {', '.join(inline)}"
    # A published query with no commit is pinned to nothing, so it would be
    # diffed against whatever is at head. The baseline could not then tell that
    # the query had been rewritten, which is the one thing it exists to catch.
    unpinned = sorted(q.label for q in queries if not q.commit_id)
    if unpinned:
        return f"queries resolved without a commit: {', '.join(unpinned)}"
    if baseline is None:
        return "no baseline from a previous run"
    if baseline.scope_fingerprint != fingerprint:
        return "the ownership scope or schema mapping changed since the last run"
    current = {q.label: q.commit_id for q in queries}
    republished = sorted(
        label
        for label, commit in current.items()
        if baseline.query_commits.get(label, commit) != commit
    )
    if republished:
        return f"queries republished since the baseline: {', '.join(republished)}"
    if full_refetch_every and baseline.runs_since_full >= full_refetch_every:
        return f"{baseline.runs_since_full} run(s) since the last full fetch"
    return ""


def row_key(row: Row, primary_key: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        str(row.get(column) if row.get(column) is not None else "") for column in primary_key
    )


def apply_diff(
    rows: Sequence[Row], entries: Iterable[Any], primary_key: Sequence[str]
) -> DiffOutcome:
    """Fold NQE diff entries into a cached table, keyed by the query's primary key.

    An entry with an ``after`` replaces or adds the row with that key; one
    without removes the row keyed by its ``before``. The primary key is what
    ``@primaryKey`` names in the published query, so the key Forward diffed on
    is the key the rows are folded on.
    """
    if not primary_key:
        raise ValueError("cannot apply a diff without a primary key")
    table: dict[tuple[str, ...], Row] = {row_key(row, primary_key): dict(row) for row in rows}
    added = modified = deleted = 0
    for entry in entries:
        after = getattr(entry, "after", None)
        before = getattr(entry, "before", None)
        if after:
            key = row_key(dict(after), primary_key)
            if key in table:
                modified += 1
            else:
                added += 1
            table[key] = dict(after)
        elif before:
            if table.pop(row_key(dict(before), primary_key), None) is not None:
                deleted += 1
    return DiffOutcome(rows=list(table.values()), added=added, modified=modified, deleted=deleted)

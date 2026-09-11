"""The rows of the last clean run, so the next run can read a snapshot diff.

Forward's incremental unit is a diff of one committed query between two
snapshots. To turn a diff into a whole table, the previous whole table has to
be at hand: this module keeps it, per query, beside a baseline record naming
the snapshot those rows came from, the ownership scope, and the commit of each
query. A later run applies diffs to the cached rows only when all of those
still match; anything else is a full fetch, and says why.

The cache is the adapter's own and is independent of infrahub-sync's run
cache. infrahub-sync always asks the adapter for whole tables, so keeping the
diff logic here works the same whether a run is driven from the command line
or from a Sync service worker that starts from an empty directory.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forward_infrahub.fetch import ResolvedQuery

Row = dict[str, Any]

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RowCache:
    """Rows and baseline for one sync, under its own directory."""

    def __init__(self, directory: str | Path, sync_name: str) -> None:
        self.root = Path(directory) / (_SAFE.sub("_", sync_name) or "forwardnetworks")

    @property
    def baseline_path(self) -> Path:
        return self.root / "baseline.json"

    def rows_path(self, key: str) -> Path:
        return self.root / "rows" / (_SAFE.sub("_", key) + ".jsonl.gz")

    def baseline(self) -> Baseline | None:
        try:
            payload = json.loads(self.baseline_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return Baseline.from_payload(payload)

    def rows(self, key: str) -> list[Row] | None:
        """The cached rows for an execution key, or ``None`` when there are none."""
        path = self.rows_path(key)
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                return [json.loads(line) for line in handle if line.strip()]
        except (OSError, ValueError):
            return None

    def commit(self, baseline: Baseline, rows_by_key: Mapping[str, Sequence[Row]]) -> None:
        """Write every table, then the baseline that vouches for them.

        The baseline goes last so a crash mid-write leaves the old baseline
        pointing at old rows, or no baseline at all, never a new baseline over
        half-written rows.
        """
        (self.root / "rows").mkdir(parents=True, exist_ok=True)
        counts: dict[str, int] = {}
        for key, rows in rows_by_key.items():
            path = self.rows_path(key)
            tmp = path.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, sort_keys=True, default=str))
                    handle.write("\n")
            tmp.replace(path)
            counts[key] = len(rows)
        payload = asdict(baseline)
        payload["tables"] = counts
        tmp = self.baseline_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.baseline_path)

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            self.baseline_path.unlink()


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

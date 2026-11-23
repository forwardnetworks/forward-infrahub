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
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

from forward_infrahub.ports import (
    Baseline,
    Row,
    apply_diff,
    full_fetch_reason,
)

__all__ = [
    "Baseline",
    "RowCache",
    "apply_diff",
    "full_fetch_reason",
]

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


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

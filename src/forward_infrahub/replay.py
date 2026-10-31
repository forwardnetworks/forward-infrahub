"""An offline stand-in for the Forward client, serving rows from a JSON file.

Used by the tests, by the Infrahub smoke test, and by operators who want to see
what a sync would do without Forward credentials. The file maps a query stem
(``forward_devices``) to its rows; an optional ``diffs`` object maps a stem to
NQE diff entries for the incremental path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from forward_sdk import ForwardNotFoundError, ForwardPaginationError
from forward_sdk.models import NqeDiffEntry
from forward_sdk.nqe import QueryRef

from forward_infrahub.queries import (
    QUERY_FILENAMES,
    query_stem,
    read_query_execution_source,
)

Row = dict[str, Any]
REPLAY_SNAPSHOT = "replay"


#: The clause a sharded execution appends after the device loop.
SHARD_CLAUSE = re.compile(r"^where device\.name in \[(?P<names>[^\]]*)\]\n", re.MULTILINE)


def _stem_of(key: str) -> str:
    return key if key.startswith("forward_") else f"forward_{key}"


def _stems(fixture: Mapping[str, Any]) -> dict[str, list[Row]]:
    tables: dict[str, list[Row]] = {}
    for key, rows in fixture.items():
        if key in {"diffs", "metrics", "snapshot_id", "unpublished"}:
            continue
        stem = key if key.startswith("forward_") else f"forward_{key}"
        tables[stem] = [dict(row) for row in rows]
    return tables


class _ReplayExecution:
    def __init__(self, rows: list[Row], *, paging_cap: int | None = None) -> None:
        self._rows = rows
        self._paging_cap = paging_cap
        self.key = "replay"

    def wait(self, **_: Any) -> dict[str, Any]:
        return {"status": "COMPLETED", "outcome": "OK", "rowsProduced": len(self._rows)}

    def rows(self, **_: Any) -> Iterator[Row]:
        if self._paging_cap is not None and len(self._rows) >= self._paging_cap:
            raise ForwardPaginationError(
                f"paging is not advancing: identical pages at offset {self._paging_cap}"
            )
        yield from (dict(row) for row in self._rows)

    stream = rows


class _ReplayRepo:
    def index(self, **_: Any) -> dict[str, Any]:
        return {}


class _ReplayNqe:
    def __init__(self, client: ReplayClient) -> None:
        self._client = client
        self.repo = _ReplayRepo()

    def resolve(self, query: QueryRef | str) -> QueryRef:
        ref = query if isinstance(query, QueryRef) else QueryRef.inline(str(query))
        if ref.mode == "path" and ref.path:
            stem = ref.path.rsplit("/", 1)[-1]
            if stem in self._client.unpublished:
                raise ForwardNotFoundError(f"{ref.path} is not in the library", status=404)
            return ref.resolved(f"FQ_replay_{stem}", REPLAY_SNAPSHOT)
        return ref

    def _stem_for(self, ref: QueryRef) -> tuple[str, list[str] | None]:
        """The fixture stem a reference names, and the device shard if it carries one."""
        if ref.mode == "path" and ref.path:
            return ref.path.rsplit("/", 1)[-1], None
        if ref.mode == "query_id" and ref.query_id and ref.query_id.startswith("FQ_replay_"):
            return ref.query_id[len("FQ_replay_") :], None
        if ref.mode == "text":
            text = ref.text or ""
            match = SHARD_CLAUSE.search(text)
            names: list[str] | None = None
            if match:
                names = [n.strip().strip('"') for n in match.group("names").split(",") if n.strip()]
                text = SHARD_CLAUSE.sub("", text)
            for filename in QUERY_FILENAMES:
                if read_query_execution_source(filename).strip() == text.strip():
                    return query_stem(filename), names
        raise ForwardNotFoundError(f"replay fixture cannot serve {ref}", status=404)

    def execute(self, query: QueryRef | str, **_: Any) -> _ReplayExecution:
        ref = query if isinstance(query, QueryRef) else QueryRef.inline(str(query))
        stem, names = self._stem_for(ref)
        rows = self._client.tables.get(stem, [])
        if names is not None:
            self._client.calls.append(f"{stem}:shard:{len(names)}")
            return _ReplayExecution(_shard_rows(stem, rows, names))
        self._client.calls.append(stem)
        return _ReplayExecution(rows, paging_cap=self._client.paging_cap)

    def query(self, query: QueryRef | str, **_: Any) -> list[Row]:
        """What ``ForwardClient.nqe.query`` returns: every row, or the paging error."""
        execution = self.execute(query)
        execution.wait()
        return list(execution.rows())

    def diff(
        self, query: QueryRef | str, *, before: str, after: str, **_: Any
    ) -> list[NqeDiffEntry]:
        ref = query if isinstance(query, QueryRef) else QueryRef.inline(str(query))
        stem, _shard = self._stem_for(ref)
        self._client.calls.append(f"diff:{stem}:{before}->{after}")
        if stem in self._client.failing_diffs:
            raise ForwardPaginationError(f"diff of {stem} exceeds the paging limit")
        return [NqeDiffEntry.model_validate(entry) for entry in self._client.diffs.get(stem, [])]


def _shard_rows(stem: str, rows: list[Row], names: list[str]) -> list[Row]:
    """The rows a device shard would have produced.

    A per-device table carries the device in ``device`` (or ``name`` for the
    devices table); an aggregated one carries its contributors in
    ``scope_devices``, and a shard sees the row with only its own contributors.
    """
    wanted = set(names)
    out: list[Row] = []
    for row in rows:
        if stem == "forward_devices":
            if str(row.get("name")) in wanted:
                out.append(dict(row))
        elif "device" in row:
            if str(row.get("device")) in wanted:
                out.append(dict(row))
        elif isinstance(row.get("scope_devices"), list):
            mine = [d for d in row["scope_devices"] if str(d) in wanted]
            if mine:
                out.append({**row, "scope_devices": mine})
        else:
            out.append(dict(row))
    return out


class _ReplaySnapshots:
    def __init__(self, client: ReplayClient) -> None:
        self._client = client

    def latest_processed_id(self, network_id: str | None = None) -> str:
        return self._client.snapshot_id

    def latest_collected_id(self, network_id: str | None = None, **_: Any) -> str:
        return self._client.snapshot_id

    def metrics(self, snapshot_id: str) -> dict[str, Any]:
        return dict(self._client.metrics)

    def list(self, *_: Any, **__: Any) -> list[Any]:
        return []


class _ReplayNetworks:
    def list(self) -> list[Any]:
        return []


class ReplayClient:
    """Serves the same calls the adapter makes against ``forward_sdk.ForwardClient``."""

    def __init__(
        self,
        fixture: Mapping[str, Any],
        *,
        unpublished: Iterable[str] = (),
        paging_cap: int | None = None,
    ) -> None:
        self.tables = _stems(fixture)
        self.diffs: dict[str, list[dict[str, Any]]] = {
            _stem_of(k): list(v) for k, v in (fixture.get("diffs") or {}).items()
        }
        self.unpublished: set[str] = {
            _stem_of(str(s)) for s in [*(fixture.get("unpublished") or []), *unpublished]
        }
        #: Stems whose diff should fail the way an oversized one does.
        self.failing_diffs: set[str] = set()
        self.paging_cap = paging_cap
        self.metrics: dict[str, Any] = dict(fixture.get("metrics") or {})
        self.snapshot_id = str(fixture.get("snapshot_id") or REPLAY_SNAPSHOT)
        self.calls: list[str] = []
        self.nqe = _ReplayNqe(self)
        self.snapshots = _ReplaySnapshots(self)
        self.networks = _ReplayNetworks()

    @classmethod
    def from_file(cls, path: str | Path) -> ReplayClient:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def close(self) -> None:
        return None

    def __enter__(self) -> ReplayClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

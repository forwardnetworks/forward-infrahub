"""An offline stand-in for the Forward client, serving rows from a JSON file.

Used by the tests, by the Infrahub smoke test, and by operators who want to see
what a sync would do without Forward credentials. The file maps a query stem
(``forward_devices``) to its rows; an optional ``diffs`` object maps a stem to
NQE diff entries for the incremental path.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from forward_sdk import ForwardNotFoundError
from forward_sdk.nqe import QueryRef

from forward_infrahub.queries import (
    QUERY_FILENAMES,
    query_stem,
    read_query_execution_source,
)

Row = dict[str, Any]
REPLAY_SNAPSHOT = "replay"


def _stems(fixture: Mapping[str, Any]) -> dict[str, list[Row]]:
    tables: dict[str, list[Row]] = {}
    for key, rows in fixture.items():
        if key in {"diffs", "metrics", "snapshot_id"}:
            continue
        stem = key if key.startswith("forward_") else f"forward_{key}"
        tables[stem] = [dict(row) for row in rows]
    return tables


class _ReplayExecution:
    def __init__(self, rows: list[Row]) -> None:
        self._rows = rows
        self.key = "replay"

    def wait(self, **_: Any) -> dict[str, Any]:
        return {"status": "COMPLETED", "outcome": "OK", "rowsProduced": len(self._rows)}

    def rows(self, **_: Any) -> Iterator[Row]:
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
            return ref.resolved(f"FQ_replay_{stem}", REPLAY_SNAPSHOT)
        return ref

    def _stem_for(self, ref: QueryRef) -> str:
        if ref.mode == "path" and ref.path:
            return ref.path.rsplit("/", 1)[-1]
        if ref.mode == "query_id" and ref.query_id and ref.query_id.startswith("FQ_replay_"):
            return ref.query_id[len("FQ_replay_") :]
        if ref.mode == "text":
            for filename in QUERY_FILENAMES:
                if read_query_execution_source(filename).strip() == (ref.text or "").strip():
                    return query_stem(filename)
        raise ForwardNotFoundError(f"replay fixture cannot serve {ref}", status=404)

    def execute(self, query: QueryRef | str, **_: Any) -> _ReplayExecution:
        ref = query if isinstance(query, QueryRef) else QueryRef.inline(str(query))
        stem = self._stem_for(ref)
        self._client.calls.append(stem)
        return _ReplayExecution(self._client.tables.get(stem, []))

    def diff(
        self, before_snapshot_id: str, after_snapshot_id: str, query: QueryRef | str, **_: Any
    ) -> list[dict[str, Any]]:
        ref = query if isinstance(query, QueryRef) else QueryRef.inline(str(query))
        stem = self._stem_for(ref)
        self._client.calls.append(f"diff:{stem}:{before_snapshot_id}->{after_snapshot_id}")
        return [dict(entry) for entry in self._client.diffs.get(stem, [])]


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

    def __init__(self, fixture: Mapping[str, Any]) -> None:
        self.tables = _stems(fixture)
        self.diffs: dict[str, list[dict[str, Any]]] = {
            (k if k.startswith("forward_") else f"forward_{k}"): list(v)
            for k, v in (fixture.get("diffs") or {}).items()
        }
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

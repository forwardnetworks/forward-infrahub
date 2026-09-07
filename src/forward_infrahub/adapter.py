"""The Forward Networks source adapter for infrahub-sync.

One run resolves a single Forward snapshot, executes each bundled query once,
scopes the rows to the devices and cloud accounts this sync owns, and hands
them to DiffSync. Several Infrahub kinds may read the same table: interfaces
become both physical and virtual interfaces, cloud networks become both VPCs
and subnets. Those share one execution.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar, TypeAlias

from diffsync import Adapter, DiffSyncModel
from diffsync.exceptions import ObjectNotFound
from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.cache.cursors import CursorState, CursorTier

from forward_infrahub.exceptions import ForwardLoadError, ForwardSafetyError
from forward_infrahub.fetch import QueryRunner, ResolvedQuery, resolve_queries
from forward_infrahub.filters import CUSTOM_FILTERS
from forward_infrahub.incremental import (
    BaselineSidecar,
    diff_rows,
    incremental_reason,
)
from forward_infrahub.queries import query_primary_key
from forward_infrahub.scope import ForwardCloudScope, ForwardDeviceScope, build_scope_fingerprint
from forward_infrahub.settings import AdapterSettings, build_client
from forward_infrahub.snapshot import ResolvedSnapshot, resolve_snapshot
from forward_infrahub.tables import (
    CLOUD_SCOPE_FILE,
    DEVICE_SCOPE_FILE,
    GENERIC_PEERS,
    AugmentContext,
    TableRef,
    parse_mapping,
)

logger = logging.getLogger(__name__)

Row = dict[str, Any]

#: Returned by reference resolution when a peer that should exist does not.
MISSING = object()


class ForwardnetworksModel(DiffSyncModelMixin, DiffSyncModel):
    """Read-only: Forward is a source of truth, never a destination."""

    @classmethod
    def create(cls, adapter: Adapter, ids: dict[Any, Any], attrs: dict[Any, Any]) -> None:
        raise NotImplementedError("forward-infrahub syncs out of Forward Networks, not into it")

    def update(self, attrs: dict[Any, Any]) -> None:
        raise NotImplementedError("forward-infrahub syncs out of Forward Networks, not into it")

    def delete(self) -> None:
        raise NotImplementedError("forward-infrahub syncs out of Forward Networks, not into it")

    @classmethod
    def _add_custom_filters(cls, native_env: Any, item: dict[str, Any]) -> None:
        native_env.filters.update(CUSTOM_FILTERS)


#: Bound before ``ForwardnetworksAdapter.type`` shadows the builtin inside the
#: class body, where ``type[...]`` would otherwise name that attribute.
ModelClass: TypeAlias = type[ForwardnetworksModel]


class ForwardnetworksAdapter(DiffSyncMixin, Adapter):
    """Loads Forward Networks data for infrahub-sync."""

    type: ClassVar[str] = "Forwardnetworks"  # type: ignore[misc]

    def __init__(
        self,
        target: str,
        adapter: SyncAdapter,
        config: SyncConfig,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.target = target
        self.config = config
        self.settings = AdapterSettings.model_validate(adapter.settings or {})
        self.client = build_client(self.settings)
        self.network_id = self.settings.resolved_network_id()

        self.snapshot: ResolvedSnapshot | None = None
        self.device_scope: ForwardDeviceScope | None = None
        self.cloud_scope: ForwardCloudScope | None = None
        self.scope_fingerprint = ""
        self.skipped: Counter[str] = Counter()

        self._runner: QueryRunner | None = None
        self._queries: dict[str, ResolvedQuery] = {}
        self._tables: dict[str, list[Row]] = {}
        self._augment = AugmentContext(settings=self.settings)
        self._prepared = False

    # ---------------------------------------------------------------- loading

    def load(self) -> None:
        self._prepare()
        super().load()
        self._log_summary()

    def _elements(self) -> list[SchemaMappingModel]:
        wanted = set(self.top_level)
        return [e for e in self.config.schema_mapping if e.name in wanted and e.mapping]

    def _prepare(self) -> None:
        """Resolve the snapshot, run every query once, then scope and augment the rows."""
        if self._prepared:
            return

        self.snapshot = resolve_snapshot(self.client, self.settings, self.network_id)
        logger.info(
            "snapshot %s (%s) is %s",
            self.snapshot.id,
            self.snapshot.selector,
            self.snapshot.completeness.status,
        )
        self._check_snapshot_gate()

        elements = self._elements()
        refs = {e.name: parse_mapping(e.mapping) for e in elements}
        keys = {key for ref in refs.values() for key in ref.execution_keys}
        self._queries = resolve_queries(self.client, sorted(keys), self.settings)
        self._runner = QueryRunner(self.client, self.settings, self.snapshot.id)

        self._load_scopes(keys)
        self._runner.run_all([self._queries[key] for key in sorted(keys)], self.network_id)

        for name, ref in refs.items():
            self._tables[name] = self._prepare_rows(ref)

        self.scope_fingerprint = build_scope_fingerprint(
            table_names=sorted(str(ref) for ref in refs.values()),
            device_vendors=self.settings.device_vendors,
            device_types=self.settings.device_types,
            device_models=self.settings.device_models,
            cloud_types=self.settings.cloud_types,
            cloud_account_ids=self.settings.cloud_account_ids,
            query_commits={q.label: q.commit_id for q in self._queries.values()},
            schema_mapping=[e.model_dump(mode="json") for e in elements],
        )
        self._prepared = True

    def _check_snapshot_gate(self) -> None:
        """Refuse a run whose snapshot is too incomplete for what it is allowed to do."""
        assert self.snapshot is not None
        if self.snapshot.complete:
            return
        reason = self.snapshot.completeness.reason
        if self.settings.require_complete_snapshot:
            raise ForwardSafetyError(
                f"snapshot {self.snapshot.id}: {reason}. Set require_complete_snapshot: false "
                "to sync anyway, or pick another snapshot."
            )
        if self._deletes_enabled() and not self.settings.allow_incomplete_snapshot_deletes:
            raise ForwardSafetyError(
                f"snapshot {self.snapshot.id}: {reason}, and this sync is configured to delete. "
                "Rows missing from an incomplete snapshot are not gone upstream, they were never "
                "collected. Keep SKIP_UNMATCHED_DST, or set "
                "allow_incomplete_snapshot_deletes: true to accept the risk."
            )
        logger.warning("snapshot %s: %s", self.snapshot.id, reason)

    def _deletes_enabled(self) -> bool:
        flags = [str(f) for f in (self.config.diffsync_flags or [])]
        return not any("SKIP_UNMATCHED_DST" in flag for flag in flags)

    def _load_scopes(self, keys: set[str]) -> None:
        """Build the device and cloud closures before any other table is scoped."""
        assert self._runner is not None
        device_key = f"file:{DEVICE_SCOPE_FILE}"
        if self.settings.device_scope_active and device_key in keys:
            rows = self._runner.run_one(self._queries[device_key], self.network_id)
            self.device_scope = ForwardDeviceScope.from_device_rows(
                rows,
                vendors=self.settings.device_vendors,
                types=self.settings.device_types,
                models=self.settings.device_models,
            )
            logger.info("device scope selected %d device(s)", len(self.device_scope.device_names))
        cloud_key = f"file:{CLOUD_SCOPE_FILE}"
        if self.settings.cloud_scope_active and cloud_key in keys:
            rows = self._runner.run_one(self._queries[cloud_key], self.network_id)
            self.cloud_scope = ForwardCloudScope.from_account_rows(
                rows,
                cloud_types=self.settings.cloud_types,
                cloud_account_ids=self.settings.cloud_account_ids,
            )
            logger.info("cloud scope selected %d account(s)", len(self.cloud_scope.account_keys))

    def _prepare_rows(self, ref: TableRef) -> list[Row]:
        assert self._runner is not None
        rows = self.scoped(ref, self._runner.rows_for_ref(ref))
        if ref.spec is not None and ref.spec.augment is not None:
            rows = ref.spec.augment(rows, self._augment)
        return rows

    def scoped(self, ref: TableRef, rows: list[Row]) -> list[Row]:
        """Drop rows outside the device or cloud closure this sync owns."""
        spec = ref.spec
        if spec is None:
            return rows
        if spec.scope == "cloud" and self.cloud_scope is not None:
            return [row for row in rows if self.cloud_scope.includes(row)]
        if spec.scope != "cloud" and self.device_scope is not None:
            column = spec.scope_column
            kept = [row for row in rows if self.device_scope.includes(row, column=column)]
            if column is None and rows:
                # Route tables carry no device column, so a filtered run cannot
                # tell which selected device contributed a prefix.
                logger.warning(
                    "table %s has no device relation; a filtered run cannot scope it and writes "
                    "nothing. Remove the device filters to sync it.",
                    spec.name,
                )
            return kept
        return rows

    # ------------------------------------------------------------ model_loader

    def _element_for(self, model_name: str) -> SchemaMappingModel:
        for element in self.config.schema_mapping:
            if element.name == model_name and element.mapping:
                return element
        raise ForwardLoadError(f"no schema_mapping entry with a mapping for {model_name!r}")

    def model_loader(self, model_name: str, model: ModelClass) -> None:
        element = self._element_for(model_name)
        rows = self._tables.get(model_name)
        if rows is None:
            self._prepare()
            rows = self._tables.get(model_name, self._prepare_rows(parse_mapping(element.mapping)))
        for payload in self._records_to_diffsync(element, model, rows):
            self.update_or_add_model_instance(model(**payload))

    def _records_to_diffsync(
        self,
        element: SchemaMappingModel,
        model: ModelClass,
        rows: list[Row],
    ) -> Iterator[dict[str, Any]]:
        """Apply the configured filters and transforms, then map rows to model fields."""
        is_source = self.config.source.name.title() == self.type.title()
        if is_source:
            selected = model.filter_records(records=rows, schema_mapping=element)
            transformed = model.transform_records(records=selected, schema_mapping=element)
            logger.info(
                "%s: loading %d/%d row(s) from %s",
                element.name,
                len(transformed),
                len(rows),
                element.mapping,
            )
        else:
            transformed = rows
        for row in transformed:
            payload = self.row_to_diffsync(row, element, model)
            if payload is not None:
                yield payload

    def row_to_diffsync(
        self,
        row: Row,
        element: SchemaMappingModel,
        model: ModelClass,
    ) -> dict[str, Any] | None:
        """Map one query row onto model fields, or ``None`` to skip it."""
        local_id = self._local_id(row, element)
        if local_id is None:
            self.skipped[f"{element.name}:empty identity"] += 1
            return None

        data: dict[str, Any] = {"local_id": local_id}
        for field in element.fields:
            if field.static is not None:
                data[field.name] = field.static
                continue
            if not field.mapping:
                continue
            value = row.get(field.mapping)
            if field.reference:
                resolved = self._resolve_reference(field.reference, value)
                if resolved is MISSING:
                    self.skipped[f"{element.name}:unresolved {field.name}"] += 1
                    return None
                data[field.name] = resolved
            elif model.is_list(name=field.name):
                data[field.name] = _as_list(value)
            elif value is not None:
                data[field.name] = value
        return data

    def _local_id(self, row: Row, element: SchemaMappingModel) -> str | None:
        """A stable per-row id, built from the query's own primary key.

        Using the query's ``@primaryKey`` means a row fetched in full and the
        same row seen in a snapshot diff produce the same id.
        """
        columns = self._primary_key_columns(element)
        parts = [str(row.get(column) or "").strip() for column in columns]
        if not parts or any(not part for part in parts):
            return None
        return "|".join(parts)

    def _primary_key_columns(self, element: SchemaMappingModel) -> tuple[str, ...]:
        ref = parse_mapping(element.mapping)
        if ref.spec is not None:
            return query_primary_key(ref.spec.query_files[0])
        if element.identifiers:
            field_map = {f.name: f.mapping for f in element.fields if f.mapping}
            return tuple(field_map[i] for i in element.identifiers if i in field_map)
        return ()

    def _resolve_reference(self, reference: str, value: Any) -> Any:
        """Turn a peer identity from the query into the peer's DiffSync id.

        The queries emit these identities directly, joined the way DiffSync
        joins identifiers, so no lookup table is needed. A value that names a
        peer which was not loaded means the row is incomplete, and the record is
        skipped rather than silently written with a missing relationship.
        """
        is_many = isinstance(value, (list, tuple))
        if value is None or (not is_many and value == ""):
            # A relationship Forward has nothing to say about, left unset.
            return None
        kinds = GENERIC_PEERS.get(reference, (reference,))
        keys = value if is_many else [value]
        resolved: list[str] = []
        for key in keys:
            text = str(key).strip()
            if not text:
                continue
            if not any(self._peer_exists(kind, text) for kind in kinds):
                return MISSING
            resolved.append(text)
        if is_many:
            return resolved
        return resolved[0] if resolved else None

    def _peer_exists(self, kind: str, identifier: str) -> bool:
        try:
            self.store.get(model=kind, identifier=identifier)
        except (ObjectNotFound, ValueError):
            return False
        return True

    def _log_summary(self) -> None:
        if self._augment.notes:
            for note in self._augment.notes:
                logger.info("%s", note)
        for reason, count in sorted(self.skipped.items()):
            logger.warning("skipped %d row(s): %s", count, reason)

    # ------------------------------------------------------------ incremental

    def cursor_tier_for(self, model_name: str) -> CursorTier:
        """Whether this run may load ``model_name`` from a snapshot diff.

        Forward diffs two snapshots of one committed query, so a run is
        incremental only when every query it uses is published and the
        ownership scope has not moved since the last run. Anything else loads
        in full, and says why.
        """
        if not self.settings.incremental:
            return CursorTier.NONE
        try:
            self._prepare()
        except Exception:
            return CursorTier.NONE
        reason = incremental_reason(
            queries=self._queries.values(),
            sidecar=BaselineSidecar.load(self._sidecar_path()),
            snapshot_id=self.snapshot.id if self.snapshot else "",
            fingerprint=self.scope_fingerprint,
        )
        if reason:
            logger.info("incremental disabled for %s: %s", model_name, reason)
            return CursorTier.NONE
        return CursorTier.TIMESTAMP

    def list_changed_since(self, model_name: str, cursor: CursorState) -> Iterator[dict[str, Any]]:
        """Yield the rows a snapshot diff reports as added or changed."""
        self._prepare()
        element = self._element_for(model_name)
        ref = parse_mapping(element.mapping)
        model: ModelClass = getattr(self, model_name)
        sidecar = BaselineSidecar.load(self._sidecar_path())
        baseline = sidecar.snapshot_id if sidecar else ""
        assert self.snapshot is not None
        if not baseline or baseline == self.snapshot.id:
            logger.info("%s: snapshot unchanged since the last run", model_name)
            return
        rows = diff_rows(
            client=self.client,
            queries=[self._queries[key] for key in ref.execution_keys],
            baseline_snapshot_id=baseline,
            snapshot_id=self.snapshot.id,
        )
        rows = self._prepare_diff_rows(ref, rows)
        yield from self._records_to_diffsync(element, model, rows)

    def _prepare_diff_rows(self, ref: TableRef, rows: list[Row]) -> list[Row]:
        rows = self.scoped(ref, rows)
        if ref.spec is not None and ref.spec.augment is not None:
            rows = ref.spec.augment(rows, self._augment)
        return rows

    def _sidecar_path(self) -> Path:
        return Path(".infrahub-sync-cache") / "forward-baseline.json"

    def write_baseline(self) -> None:
        """Record the snapshot a later incremental run may diff against."""
        assert self.snapshot is not None
        BaselineSidecar(
            snapshot_id=self.snapshot.id,
            scope_fingerprint=self.scope_fingerprint,
            query_commits={q.label: q.commit_id for q in self._queries.values()},
        ).save(self._sidecar_path())


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        return [part.strip() for part in text.split(",")] if "," in text else [text]
    return [value]

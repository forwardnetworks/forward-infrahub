"""The Forward Networks source adapter for infrahub-sync.

One run resolves a single Forward snapshot, executes each bundled query once,
scopes the rows to the devices and cloud accounts this sync owns, and hands
them to DiffSync. Several Infrahub kinds may read the same table: interfaces
become both physical and virtual interfaces, cloud networks become both VPCs
and subnets. Those share one execution.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from collections.abc import Iterator
from typing import Any, ClassVar, Literal, TypeAlias

from diffsync import Adapter, DiffSyncModel
from diffsync.exceptions import ObjectNotFound
from forward_sdk import ForwardError
from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)

from forward_infrahub.capabilities import register as register_capabilities
from forward_infrahub.exceptions import ForwardLoadError, ForwardSafetyError
from forward_infrahub.fetch import QueryRunner, ResolvedQuery, resolve_queries
from forward_infrahub.filters import CUSTOM_FILTERS
from forward_infrahub.queries import query_primary_key
from forward_infrahub.rowcache import (
    Baseline,
    RowCache,
    apply_diff,
    full_fetch_reason,
    now_iso,
)
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

# infrahub-sync resolves this module through the entry point, so importing it
# is the moment to declare the adapter to the capability table (see
# capabilities.py for why that table needs to be told).
register_capabilities()

Row = dict[str, Any]
FetchMode = Literal["full", "diff", "unchanged"]

#: Returned by reference resolution when a peer that should exist does not.
MISSING = object()

#: Set to any non-empty value to force the next run to fetch everything.
ENV_FULL_REFETCH = "FORWARD_FULL_REFETCH"


class _LocalIdBase(DiffSyncModel):
    """Declares ``local_id`` once, one level below the mixin.

    ``DiffSyncModelMixin`` carries ``local_id`` as a plain class attribute.
    A pydantic model that redeclares it as a field warns that it shadows the
    parent; declaring it here, on a real model base, gives every subclass the
    field without the warning.
    """

    local_id: str | None = None


class ForwardnetworksModel(DiffSyncModelMixin, _LocalIdBase):
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
        self.cache = RowCache(self.settings.cache_directory, config.name or "forwardnetworks")
        #: How this run got its rows, and why, once ``_prepare`` has run.
        self.fetch_mode: FetchMode = "full"
        self.fetch_reason = ""

        self._runner: QueryRunner | None = None
        self._queries: dict[str, ResolvedQuery] = {}
        #: Prepared rows per mapping entry, keyed by the entry's identity
        #: because one kind may be mapped by more than one entry.
        self._tables: dict[int, list[Row]] = {}
        self._augment = AugmentContext(settings=self.settings)
        self._prepared = False

    # ---------------------------------------------------------------- loading

    def load(self) -> None:
        self._prepare()
        super().load()
        self._log_summary()
        self.commit_cache()

    def _elements(self) -> list[SchemaMappingModel]:
        wanted = set(self.top_level)
        return [e for e in self.config.schema_mapping if e.name in wanted and e.mapping]

    def _elements_for(self, model_name: str) -> list[SchemaMappingModel]:
        """Every mapping entry that loads this kind.

        A kind may be mapped more than once: a reference names one peer kind,
        so rows whose peer differs in kind are split across entries that filter
        them apart. Each entry contributes its own rows to the same kind.
        """
        found = [e for e in self.config.schema_mapping if e.name == model_name and e.mapping]
        if not found:
            raise ForwardLoadError(f"no schema_mapping entry with a mapping for {model_name!r}")
        return found

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
        refs = {id(e): parse_mapping(e.mapping) for e in elements}
        keys = {key for ref in refs.values() for key in ref.execution_keys}
        self._queries = resolve_queries(self.client, sorted(keys), self.settings)
        self._runner = QueryRunner(self.client, self.settings, self.snapshot.id)
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

        self._fetch_rows(sorted(keys), refs)
        self._load_scopes(keys)
        for element in elements:
            self._tables[id(element)] = self._prepare_rows(refs[id(element)])
        self._prepared = True

    # ------------------------------------------------------------- fetching

    def _fetch_rows(self, keys: list[str], refs: dict[int, TableRef]) -> None:
        """Fill the runner with every table, from Forward or from the cache plus a diff.

        A full fetch is the exception: the first run, a moved scope, a
        republished query, the refetch cadence, or a request for one. Otherwise
        the previous run's rows are the baseline, and only what changed between
        its snapshot and this one is read from Forward.
        """
        assert self._runner is not None and self.snapshot is not None
        baseline = self.cache.baseline()
        reason = full_fetch_reason(
            queries=self._queries.values(),
            baseline=baseline,
            fingerprint=self.scope_fingerprint,
            incremental=self.settings.incremental,
            full_refetch_every=self.settings.full_refetch_every,
        )
        if os.environ.get(ENV_FULL_REFETCH):
            reason = f"{ENV_FULL_REFETCH} is set"
        if reason or baseline is None:
            self.fetch_mode, self.fetch_reason = "full", reason
            logger.info("full fetch: %s", reason)
            self._fetch_full(keys)
            return

        primary_keys = self._primary_keys_by_execution(refs)
        if baseline.snapshot_id == self.snapshot.id:
            self.fetch_mode, self.fetch_reason = (
                "unchanged",
                "snapshot unchanged since the last run",
            )
            logger.info(
                "snapshot %s is the baseline; nothing to read from Forward", self.snapshot.id
            )
            self._fetch_cached(keys, baseline)
            return

        self.fetch_mode, self.fetch_reason = "diff", f"{baseline.snapshot_id} -> {self.snapshot.id}"
        self._fetch_diff(keys, baseline, primary_keys)

    def _fetch_full(self, keys: list[str]) -> None:
        assert self._runner is not None
        device_key = f"file:{DEVICE_SCOPE_FILE}"
        if device_key in keys:
            # Devices first, unsharded: they are the universe any query that
            # could not be paged to completion is then partitioned over.
            rows = self._runner.run_one(self._queries[device_key], self.network_id)
            self._runner.device_names = sorted(str(r.get("name") or "") for r in rows)
        self._runner.run_all([self._queries[key] for key in keys], self.network_id)

    def _fetch_cached(self, keys: list[str], baseline: Baseline) -> None:
        assert self._runner is not None
        missing: list[str] = []
        for key in keys:
            rows = self.cache.rows(key) if key in baseline.tables else None
            if rows is None:
                missing.append(key)
            else:
                self._runner.seed(key, rows)
        if missing:
            logger.info("not in the cache, fetching in full: %s", ", ".join(missing))
            self._fetch_full(missing)

    def _fetch_diff(
        self, keys: list[str], baseline: Baseline, primary_keys: dict[str, tuple[str, ...]]
    ) -> None:
        assert self._runner is not None and self.snapshot is not None
        device_key = f"file:{DEVICE_SCOPE_FILE}"
        ordered = (
            [device_key, *[k for k in keys if k != device_key]] if device_key in keys else keys
        )
        for key in ordered:
            query = self._queries[key]
            cached = self.cache.rows(key) if key in baseline.tables else None
            if cached is None or not primary_keys.get(key):
                logger.info("%s: no cached rows to diff against; fetching in full", query.label)
                self._runner.run_one(query, self.network_id)
            else:
                try:
                    entries = self._runner.diff(query, baseline.snapshot_id, self.snapshot.id)
                except ForwardError as exc:
                    # A whole table read fresh is always right; a diff that
                    # failed is not. Say so, and read it whole.
                    logger.warning(
                        "%s: diff %s -> %s failed (%s); fetching in full",
                        query.label,
                        baseline.snapshot_id,
                        self.snapshot.id,
                        exc,
                    )
                    self._runner.run_one(query, self.network_id)
                else:
                    outcome = apply_diff(cached, entries, primary_keys[key])
                    logger.info(
                        "%s: %s row(s) between %s and %s",
                        query.label,
                        outcome.summary,
                        baseline.snapshot_id,
                        self.snapshot.id,
                    )
                    self._runner.seed(key, outcome.rows)
            if key == device_key:
                rows = self._runner.rows_for(key)
                self._runner.device_names = sorted(str(r.get("name") or "") for r in rows)

    def _primary_keys_by_execution(self, refs: dict[int, TableRef]) -> dict[str, tuple[str, ...]]:
        """The columns a diff folds on, per execution key.

        The query's own key, not the table's: a diff arrives in the shape
        Forward produced, before any augmentation reshapes it.
        """
        keys: dict[str, tuple[str, ...]] = {}
        for element in self._elements():
            ref = refs[id(element)]
            for key in ref.execution_keys:
                if ref.spec is not None:
                    filename = key.split(":", 1)[1]
                    keys[key] = query_primary_key(filename)
                else:
                    keys.setdefault(key, self._primary_key_columns(element))
        return keys

    def commit_cache(self) -> None:
        """Record this run's rows and snapshot as the baseline for the next.

        Skipped for an incomplete snapshot: its rows understate the network,
        and a diff from them would report every uncollected device as new.
        """
        assert self.snapshot is not None and self._runner is not None
        if not self.snapshot.complete:
            logger.warning(
                "snapshot %s is incomplete; the baseline stays at the last complete one",
                self.snapshot.id,
            )
            return
        previous = self.cache.baseline()
        runs_since_full = (
            0 if self.fetch_mode == "full" else (previous.runs_since_full + 1 if previous else 1)
        )
        self.cache.commit(
            Baseline(
                snapshot_id=self.snapshot.id,
                scope_fingerprint=self.scope_fingerprint,
                query_commits={q.label: q.commit_id for q in self._queries.values()},
                loaded_at=now_iso(),
                runs_since_full=runs_since_full,
            ),
            self._runner.cached(),
        )

    def _check_snapshot_gate(self) -> None:
        """Refuse, or warn about, a snapshot that understates the network.

        infrahub-sync never deletes, so an incomplete snapshot cannot remove
        anything from Infrahub. It can still leave new rows unwritten and
        it is never adopted as a diff baseline, so a warning is the floor.
        """
        assert self.snapshot is not None
        if self.snapshot.complete:
            return
        reason = self.snapshot.completeness.reason
        if self.settings.require_complete_snapshot:
            raise ForwardSafetyError(
                f"snapshot {self.snapshot.id}: {reason}. Set require_complete_snapshot: false "
                "to sync anyway, or pick another snapshot."
            )
        logger.warning("snapshot %s: %s", self.snapshot.id, reason)

    def _load_scopes(self, keys: set[str]) -> None:
        """Build the device and cloud closures before any table is scoped."""
        assert self._runner is not None
        device_key = f"file:{DEVICE_SCOPE_FILE}"
        if self.settings.device_scope_active and device_key in keys:
            rows = self._runner.rows_for(device_key)
            self.device_scope = ForwardDeviceScope.from_device_rows(
                rows,
                vendors=self.settings.device_vendors,
                types=self.settings.device_types,
                models=self.settings.device_models,
            )
            logger.info("device scope selected %d device(s)", len(self.device_scope.device_names))
        cloud_key = f"file:{CLOUD_SCOPE_FILE}"
        if self.settings.cloud_scope_active and cloud_key in keys:
            rows = self._runner.rows_for(cloud_key)
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

    def model_loader(self, model_name: str, model: ModelClass) -> None:
        self._prepare()
        for element in self._elements_for(model_name):
            rows = self._tables.get(id(element))
            if rows is None:
                rows = self._prepare_rows(parse_mapping(element.mapping))
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
            return ref.spec.primary_key or query_primary_key(ref.spec.query_files[0])
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

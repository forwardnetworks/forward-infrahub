"""The bundled NQE queries, read from the package, and the query catalogue over them."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import resources
from pathlib import Path

from forward_sdk.nqe import files
from forward_sdk.nqe.files import load_query, strip_primary_key

from forward_infrahub.ports import QUERY_FILENAMES, library_path, query_stem

__all__ = [
    "QUERY_FILENAMES",
    "BundledQueries",
    "SdkQueryContractReader",
    "all_query_sources",
    "library_path",
    "query_stem",
    "read_query_execution_source",
    "read_query_source",
]


def read_query_source(filename: str) -> str:
    """The query exactly as bundled, ready to publish to the library."""
    if filename not in QUERY_FILENAMES:
        raise ValueError(f"unknown bundled query: {filename}")
    return (resources.files(__name__) / filename).read_text(encoding="utf-8")


def read_query_execution_source(filename: str) -> str:
    """The query without ``@primaryKey``, which the run endpoints reject."""
    return strip_primary_key(read_query_source(filename))


def all_query_sources() -> list[tuple[str, str]]:
    return [(name, read_query_source(name)) for name in QUERY_FILENAMES]


class BundledQueries:
    """The query catalogue port over the queries shipped in this package."""

    def bundled_source(self, filename: str) -> str:
        return read_query_source(filename)

    def file_source(self, path: str) -> str:
        return load_query(Path(path))


class SdkQueryContractReader:
    """The query contract reader over the Forward SDK's query-file helpers."""

    def contract_version(self, source: str) -> str | None:
        return files.contract_version(source)

    def missing_fields(self, source: str, expected: Sequence[str]) -> Sequence[str]:
        return files.missing_fields(source, expected)

    def select_field_sets(self, source: str) -> Sequence[Sequence[str]]:
        return files.select_field_sets(source)

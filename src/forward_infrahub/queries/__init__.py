"""The bundled NQE queries, read from the package, and the query catalogue over them."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from forward_sdk.nqe.files import load_query, strip_primary_key

from forward_infrahub.ports import QUERY_FILENAMES, library_path, query_stem

__all__ = [
    "QUERY_FILENAMES",
    "BundledQueries",
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

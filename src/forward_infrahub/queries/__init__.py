"""The bundled NQE queries and how to read them."""

from __future__ import annotations

from importlib import resources

from forward_sdk.nqe.files import strip_primary_key

from forward_infrahub.contracts import QUERY_CONTRACT_FIELDS, primary_key_columns

QUERY_FILENAMES: tuple[str, ...] = tuple(sorted(QUERY_CONTRACT_FIELDS))


def read_query_source(filename: str) -> str:
    """The query exactly as bundled, ready to publish to the library."""
    if filename not in QUERY_FILENAMES:
        raise ValueError(f"unknown bundled query: {filename}")
    return (resources.files(__name__) / filename).read_text(encoding="utf-8")


def read_query_execution_source(filename: str) -> str:
    """The query without ``@primaryKey``, which the run endpoints reject."""
    return strip_primary_key(read_query_source(filename))


def query_primary_key(filename: str) -> tuple[str, ...]:
    return primary_key_columns(read_query_source(filename))


def query_stem(filename: str) -> str:
    return filename[: -len(".nqe")] if filename.endswith(".nqe") else filename


def library_path(directory: str, filename: str) -> str:
    """Where a bundled query lives in the org repository once published."""
    return f"{directory.rstrip('/')}/{query_stem(filename)}"


def all_query_sources() -> list[tuple[str, str]]:
    return [(name, read_query_source(name)) for name in QUERY_FILENAMES]

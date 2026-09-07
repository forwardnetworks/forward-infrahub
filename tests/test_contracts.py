"""The bundled queries must keep producing the columns the mappings read."""

from __future__ import annotations

import re

import yaml

from forward_infrahub.contracts import QUERY_CONTRACT_FIELDS, check_contracts, primary_key_columns
from forward_infrahub.nqe_types import (
    DEVICE_TYPE,
    KNOWN_PREFIXES,
    OS,
    TYPE_SOURCES,
    VENDOR,
)
from forward_infrahub.queries import (
    QUERY_FILENAMES,
    all_query_sources,
    read_query_execution_source,
    read_query_source,
)
from forward_infrahub.tables import TABLES, parse_mapping

from .conftest import EXAMPLE_DIR


def test_every_bundled_query_matches_its_contract() -> None:
    assert check_contracts(all_query_sources()) == []


def test_every_contract_has_a_file() -> None:
    for filename in QUERY_CONTRACT_FIELDS:
        assert read_query_source(filename).strip()


def test_execution_source_drops_only_the_primary_key() -> None:
    for filename in QUERY_FILENAMES:
        source = read_query_source(filename)
        execution = read_query_execution_source(filename)
        assert "@primaryKey" not in execution
        assert len(execution) < len(source)
        without_pk = [line for line in source.splitlines() if "@primaryKey" not in line]
        assert execution.splitlines() == without_pk


def test_primary_keys_are_declared() -> None:
    for filename in QUERY_FILENAMES:
        assert primary_key_columns(read_query_source(filename)), filename


def test_every_table_names_bundled_files() -> None:
    for spec in TABLES.values():
        for filename in spec.query_files:
            assert filename in QUERY_FILENAMES, spec.name


def test_tables_sharing_a_query_share_a_primary_key() -> None:
    """A table backed by several queries must key its rows the same way in each."""
    for spec in TABLES.values():
        keys = {primary_key_columns(read_query_source(f)) for f in spec.query_files}
        assert len(keys) == 1, spec.name


def test_example_config_only_reads_contract_columns() -> None:
    """The drift gate: every column the example mapping names must exist.

    A transform writes its result back into the row under its own field name,
    so a transform target counts as an available column even though no query
    produces it.
    """
    config = yaml.safe_load((EXAMPLE_DIR / "config.yml").read_text())
    problems: list[str] = []
    for element in config["schema_mapping"]:
        ref = parse_mapping(element["mapping"])
        assert ref.spec is not None
        columns: set[str] = set()
        for filename in ref.spec.query_files:
            columns |= set(QUERY_CONTRACT_FIELDS[filename])
        columns |= {t["field"] for t in element.get("transforms") or []}
        named = [f["mapping"] for f in element["fields"] if f.get("mapping")]
        named += [f["field"] for f in element.get("filters") or []]
        for column in named:
            if column not in columns:
                problems.append(f"{element['name']}: {column!r} is not in {ref.spec.name}")
    assert problems == []


def test_transforms_only_read_columns_that_exist() -> None:
    """A transform expression may only reference columns the query produces."""
    import re

    config = yaml.safe_load((EXAMPLE_DIR / "config.yml").read_text())
    problems: list[str] = []
    for element in config["schema_mapping"]:
        ref = parse_mapping(element["mapping"])
        assert ref.spec is not None
        columns: set[str] = set()
        for filename in ref.spec.query_files:
            columns |= set(QUERY_CONTRACT_FIELDS[filename])
        for transform in element.get("transforms") or []:
            for name in re.findall(r"{{\s*(\w+)", transform["expression"]):
                if name not in columns:
                    problems.append(
                        f"{element['name']}: transform reads {name!r}, absent from {ref.spec.name}"
                    )
    assert problems == []


#: A stripped prefix, ``"OS."``, or a member compared as a literal,
#: ``"NetworkInstanceType.DEFAULT_INSTANCE"``. Both name an NQE type, and a
#: wrong name fails differently but silently in each: the strip does nothing,
#: while the comparison is against a string the field can never equal, so a
#: `!=` test is always true and the rows it was meant to exclude come through.
TYPE_NAME_RE = re.compile(r'"([A-Z][A-Za-z0-9]*)\.[A-Z_]*"')


def type_names_used() -> set[str]:
    return {
        f"{name}."
        for filename in QUERY_FILENAMES
        for name in TYPE_NAME_RE.findall(read_query_source(filename))
    }


def test_no_query_names_a_type_of_unknown_provenance() -> None:
    """An NQE type name cannot be inferred, so each one records its source.

    Getting one wrong is invisible offline: the value still reaches Infrahub and
    still round-trips, and the fixture that would catch it was written by
    whoever wrote the assumption, so it agrees.
    """
    unknown = type_names_used() - KNOWN_PREFIXES
    assert unknown == set(), (
        f"queries name the NQE types {sorted(unknown)}, whose provenance is unrecorded. Look "
        "each one up in the NQE data-model reference and record where it came from."
    )


def test_every_recorded_type_is_used_somewhere() -> None:
    """So the record shrinks with the code rather than going stale.

    A name reaches NQE either inside a query literal or as an argument to a
    predicate builder from Python, so both are counted.
    """
    used = type_names_used() | {f"{name}." for name in (VENDOR, DEVICE_TYPE, OS)}
    unused = KNOWN_PREFIXES - used
    assert unused == set(), f"{sorted(unused)} is recorded but nothing uses it"


def test_every_type_records_where_its_name_came_from() -> None:
    assert all(source.strip() for source in TYPE_SOURCES.values())
    assert set(KNOWN_PREFIXES) == {f"{name}." for name in TYPE_SOURCES}


def test_location_backed_kinds_filter_out_empty_sites() -> None:
    """Forward returns the whole location catalogue, not this network's sites.

    On a real 37-device network that is 402 locations, 389 of them holding no
    devices, so importing them unfiltered fills Infrahub with sites that exist
    only in Forward's address book.
    """
    config = yaml.safe_load((EXAMPLE_DIR / "config.yml").read_text())
    for element in config["schema_mapping"]:
        if element["mapping"] == "nqe:locations":
            filters = element.get("filters") or []
            assert any(
                f["field"] == "scope_devices" and f["operation"] == "is_not_empty" for f in filters
            ), f"{element['name']} would import every location Forward knows"


def test_vlan_names_are_qualified_because_infrahub_keys_on_them() -> None:
    """IpamVLAN inherits a human-friendly id of name alone.

    Two VLANs sharing a name are then the same node to Infrahub however they
    are scoped, and the second write updates the first rather than being
    rejected. A real network had 127 VLANs under 57 names.
    """
    config = yaml.safe_load((EXAMPLE_DIR / "config.yml").read_text())
    vlan = next(e for e in config["schema_mapping"] if e["name"] == "IpamVLAN")
    name_field = next(f for f in vlan["fields"] if f["name"] == "name")
    transform = next(t for t in vlan["transforms"] if t["field"] == name_field["mapping"])
    # The identifying columns must all appear, or two VLANs can still collide.
    for column in ("vid", "site"):
        assert f"{{{{ {column} }}}}" in transform["expression"], column

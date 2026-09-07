"""The bundled queries must keep producing the columns the mappings read."""

from __future__ import annotations

import re

import yaml

from forward_infrahub.contracts import QUERY_CONTRACT_FIELDS, check_contracts, primary_key_columns
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


#: NQE type names read off ``toString()`` renderings on a real network.
#: The NQE data model names its types independently of the REST schema, so a
#: name cannot be inferred from the SDK class: ``device.platform.os`` is an
#: ``OS`` in NQE and a ``VendorOs`` in the SDK. Inferring it that way is how
#: the platform name was wrong, and the inference held for the two types that
#: were checked first.
MEASURED_TYPE_PREFIXES = {"Vendor.", "DeviceType.", "OS.", "NetworkInstanceType."}

#: Assumed but never seen rendered. Each is a name that may not exist, in which
#: case the replace does nothing and the prefix reaches Infrahub inside the
#: value. Both sides of a reference use the same expression, so identities
#: still match and a sync still works; the names are simply wrong. Confirm each
#: against a live network and move it above, or correct it.
UNVERIFIED_TYPE_PREFIXES = {"CloudType."}

TYPE_PREFIX_RE = re.compile(r'replace\(toString\([^)]*\),\s*"([^"]*)"')


def stripped_prefixes() -> set[str]:
    return {
        prefix
        for filename in QUERY_FILENAMES
        for prefix in TYPE_PREFIX_RE.findall(read_query_source(filename))
    }


def test_no_query_strips_an_unconsidered_prefix() -> None:
    """A prefix that never appears makes replace a silent no-op.

    That is the failure this guards. The value still reaches Infrahub, still
    round-trips, and is wrong in a way no offline test can see, because the
    fixture that would catch it was written by whoever wrote the assumption.
    A new prefix must be measured, or listed as unverified deliberately.
    """
    unconsidered = stripped_prefixes() - MEASURED_TYPE_PREFIXES - UNVERIFIED_TYPE_PREFIXES
    assert unconsidered == set(), (
        f"queries strip {sorted(unconsidered)}, which is neither measured nor listed as "
        "unverified. Read the type name off a toString() rendering on a real network."
    )


def test_the_unverified_list_still_describes_the_queries() -> None:
    """So the list shrinks when a prefix is confirmed, rather than going stale."""
    assert stripped_prefixes() >= UNVERIFIED_TYPE_PREFIXES

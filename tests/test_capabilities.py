"""The capability declaration infrahub-sync v3 validates registered packages against.

infrahub-sync keeps that table in-tree and closed, so this package inserts its
own record at import. These tests hold the record to the adapter it describes:
a setting cannot be added without being declared, and the incremental flag
cannot drift from what the adapter actually overrides.
"""

from __future__ import annotations

import re

import infrahub_sync.configuration.capabilities as table_module
from infrahub_sync import DiffSyncMixin
from infrahub_sync.configuration import validation

from forward_infrahub import capabilities
from forward_infrahub.adapter import ForwardnetworksAdapter
from forward_infrahub.settings import AdapterSettings

SETTING_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def test_every_setting_is_declared_and_nothing_else() -> None:
    declared = capabilities.declaration()["allowed_settings"]
    assert declared == frozenset(AdapterSettings.model_fields)


def test_credential_paths_are_settings_with_valid_names() -> None:
    declared = capabilities.declaration()
    for path in declared["credential_setting_paths"]:
        assert path in declared["allowed_settings"]
    for name in declared["allowed_settings"]:
        assert SETTING_NAME.fullmatch(name), name


def test_incremental_flag_matches_what_the_adapter_overrides() -> None:
    """infrahub-sync's own conformance rule, applied to us.

    The flag says whether the adapter takes part in infrahub-sync's cursor
    protocol. Diffs here happen inside the adapter's load instead, so the
    protocol must not be overridden while the flag says it is not.
    """
    overrides = ForwardnetworksAdapter.cursor_tier_for is not DiffSyncMixin.cursor_tier_for
    assert capabilities.declaration()["incremental_extraction"] is overrides


def test_importing_the_adapter_registers_the_declaration() -> None:
    assert capabilities.is_registered()
    record = table_module.BUILTIN_ADAPTER_CAPABILITIES[capabilities.ADAPTER_NAME]
    assert record.roles == frozenset({"source"})
    assert record.allowed_settings == frozenset(AdapterSettings.model_fields)


def test_every_module_holding_the_table_sees_the_same_one() -> None:
    """The validator reads its own by-value binding, not the defining module's."""
    bound = getattr(validation, capabilities.TABLE_ATTRIBUTE)
    assert bound is table_module.BUILTIN_ADAPTER_CAPABILITIES
    assert capabilities.ADAPTER_NAME in bound


def test_registering_twice_changes_nothing() -> None:
    before = table_module.BUILTIN_ADAPTER_CAPABILITIES
    assert capabilities.register() is True
    assert table_module.BUILTIN_ADAPTER_CAPABILITIES is before

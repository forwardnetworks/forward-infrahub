"""What this adapter declares about its own configuration, for infrahub-sync v3.

A registered configuration package is validated against a table of adapter
capabilities that infrahub-sync ships as a closed, in-tree mapping. An adapter
absent from it fails validation with ``missing-adapter`` before any adapter
code is imported, and nothing consults entry points for the declaration. So
this package builds the same record and inserts it into that table when it is
imported, which is the only way an out-of-tree adapter can be registered today.

The table is a ``MappingProxyType`` bound by value in several infrahub-sync
modules. Rebinding is done by identity, so every module holding the original
proxy object sees the replacement, whichever of them the validator reads from.
"""

from __future__ import annotations

import sys
from types import MappingProxyType
from typing import Any

from forward_infrahub.settings import AdapterSettings

ADAPTER_NAME = "forwardnetworks"
TABLE_ATTRIBUTE = "BUILTIN_ADAPTER_CAPABILITIES"
CREDENTIAL_SETTINGS: tuple[str, ...] = ("username", "password")


def declaration() -> dict[str, Any]:
    """The capability record as plain data, independent of infrahub-sync's class.

    Kept as a dict so it can be built and tested without the class, which
    exists only on the v3 line. ``incremental_extraction`` is ``False`` on
    purpose: snapshot-to-snapshot diffs happen inside this adapter's own load,
    not through infrahub-sync's cursor protocol, and infrahub-sync's own
    conformance rule ties the flag to whether that protocol is overridden.
    """
    return {
        "adapter_name": ADAPTER_NAME,
        "roles": frozenset({"source"}),
        "allowed_settings": frozenset(AdapterSettings.model_fields),
        "credential_setting_paths": CREDENTIAL_SETTINGS,
        "incremental_extraction": False,
        "contract_version": 1,
    }


def build() -> Any:
    """The record in infrahub-sync's own type.

    Raises:
        ImportError: the installed infrahub-sync predates capability
            declarations, so there is nothing to build for.
    """
    from infrahub_sync.configuration.capabilities import AdapterConfigurationCapabilities

    return AdapterConfigurationCapabilities(**declaration())


def register() -> bool:
    """Insert this adapter's declaration into infrahub-sync's capability table.

    Returns ``True`` when the table carries the declaration afterwards, whether
    this call added it or it was already there, and ``False`` when the
    installed infrahub-sync has no such table. Idempotent.
    """
    try:
        import infrahub_sync.configuration.capabilities as table_module
    except ImportError:
        return False
    current = getattr(table_module, TABLE_ATTRIBUTE)
    if ADAPTER_NAME in current:
        return True
    updated = MappingProxyType({**current, ADAPTER_NAME: build()})
    # Every infrahub-sync module that imported the table by value holds the
    # same proxy object; rebinding by identity reaches all of them, including
    # any not known here. Modules imported later import the rebound name.
    for module in list(sys.modules.values()):
        if not getattr(module, "__name__", "").startswith("infrahub_sync"):
            continue
        if getattr(module, TABLE_ATTRIBUTE, None) is current:
            setattr(module, TABLE_ATTRIBUTE, updated)
    return True


def is_registered() -> bool:
    try:
        import infrahub_sync.configuration.capabilities as table_module
    except ImportError:
        return False
    return ADAPTER_NAME in getattr(table_module, TABLE_ATTRIBUTE)

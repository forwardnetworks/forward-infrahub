"""NQE type names, and where each one came from.

The NQE data model is a separate namespace from the REST schema, so a type name
cannot be inferred from the SDK class that models the same field:
``device.platform.os`` is an ``OS`` in NQE and a ``VendorOs`` in the SDK. That
inference is how platform names were briefly wrong, and it held for the two
types that happened to be checked first, which is what made it convincing.

Names are published in Forward's NQE data-model reference, reachable from the
Documentation tab of the NQE Library in an instance; the public documentation
site points there rather than carrying it. So a name is looked up rather than
guessed, and ``toString()`` on a real network confirms it rather than reveals
it.

Getting one wrong is close to invisible. Stripping a prefix that never appears
does nothing, and the value reaches Infrahub carrying it. Comparing against a
literal of the wrong type yields a string the field can never equal, so a ``!=``
test is always true and the rows it was meant to exclude come through. Neither
shows up offline, because the fixture that would catch it is written by whoever
wrote the assumption.
"""

from __future__ import annotations

#: NQE type name to how this project came to trust it.
TYPE_SOURCES: dict[str, str] = {
    "Vendor": "rendered by toString on a live network",
    "DeviceType": "rendered by toString on a live network",
    "OS": "rendered by toString on a live network",
    "NetworkInstanceType": "compared as a literal by the Forward Nautobot queries in production",
    "CloudType": "NQE data-model reference, via the forward-sdk maintainers",
}

VENDOR = "Vendor"
DEVICE_TYPE = "DeviceType"
OS = "OS"

#: The ``Type.`` form, as it appears inside a query's string literals.
KNOWN_PREFIXES: frozenset[str] = frozenset(f"{name}." for name in TYPE_SOURCES)

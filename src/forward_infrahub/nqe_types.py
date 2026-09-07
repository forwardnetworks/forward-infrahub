"""NQE type names, and where each one came from.

The NQE data model is a separate namespace from the REST schema, so a type name
cannot be inferred from the SDK class that models the same field:
``device.platform.os`` is an ``OS`` in NQE and a ``VendorOs`` in the SDK. That
inference is how platform names were briefly wrong, and it held for the two
types that happened to be checked first, which is what made it convincing.

Names are published, one page per type, under
https://docs.fwd.app/latest/application/nqe/language/data-model/ (``type-os``,
``type-vendor``, and so on). So a name is looked up rather than guessed, and
``toString()`` on a real network confirms it rather than reveals it. There is
no ``type-vendoros`` page, which is the tell that ``VendorOs`` was only ever a
REST model name.

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
    "Vendor": "NQE data-model reference type-vendor, and rendered by toString on a network",
    "DeviceType": "rendered by toString on a network",
    "OS": "NQE data-model reference type-os, and rendered by toString on a network",
    "NetworkInstanceType": "NQE data-model reference type-networkinstancetype",
    "CloudType": "NQE data-model reference type-cloudtype",
}

#: NQE member names that the SDK's enum does not carry, and SDK member names
#: that NQE does not. The SDK generates its enums from the REST schema, and the
#: two namespaces disagree about more than type names: for the same vendors,
#: NQE says AZURE and GENERAL_DYNAMICS where REST says MICROSOFT and GD, and
#: REST carries an IBM that the NQE type does not list.
#:
#: This matters because member names are what a predicate is built from. Using
#: the REST spelling produces ``Vendor.GD``, which NQE has no member for, and
#: rejecting ``GENERAL_DYNAMICS`` refuses a vendor that is perfectly valid
#: there. Both were happening before these were recorded.
#:
#: Read from the published NQE type pages under the data-model reference, which
#: are the authority for the NQE namespace:
#: https://docs.fwd.app/latest/application/nqe/language/data-model/type-vendor
NQE_ONLY_MEMBERS: dict[str, frozenset[str]] = {
    "Vendor": frozenset({"AZURE", "GENERAL_DYNAMICS"}),
}

#: The mirror image: names the SDK enum has that the NQE type does not.
REST_ONLY_MEMBERS: dict[str, frozenset[str]] = {
    "Vendor": frozenset({"MICROSOFT", "GD", "IBM"}),
}


def nqe_members(enum_type: str, sdk_values: set[str]) -> set[str]:
    """The member names NQE will accept, given the SDK's idea of them.

    The SDK enum is the best offline approximation of an NQE type's members,
    and it is only an approximation: it is generated from a different namespace.
    """
    return (sdk_values | set(NQE_ONLY_MEMBERS.get(enum_type, ()))) - set(
        REST_ONLY_MEMBERS.get(enum_type, ())
    )


VENDOR = "Vendor"
DEVICE_TYPE = "DeviceType"
OS = "OS"

#: The ``Type.`` form, as it appears inside a query's string literals.
KNOWN_PREFIXES: frozenset[str] = frozenset(f"{name}." for name in TYPE_SOURCES)

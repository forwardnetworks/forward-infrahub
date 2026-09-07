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

#: The members of the NQE enums this project builds predicates from, recorded
#: from their pages in the data-model reference.
#:
#: These are not derived from ``forward_sdk.models``. The SDK generates its
#: enums from the REST schema, which is a different namespace with different
#: contents, and the difference is not a rounding error: NQE's ``DeviceType``
#: has 16 members describing what a device does, while the REST one has 75 and
#: includes cloud constructs like ``AWS_SUBNET``. For ``Vendor`` the lists are
#: close but disagree on three names, where NQE says ``AZURE`` and
#: ``GENERAL_DYNAMICS`` and REST says ``MICROSOFT``, ``GD`` and ``IBM``.
#:
#: Member names are what a predicate is built from, so using the REST list
#: refuses values that are valid here and accepts values Forward rejects.
#: Measured against a live network: ``Vendor.GENERAL_DYNAMICS`` is accepted and
#: ``Vendor.GD`` fails with "Unknown alternative GD".
#:
#: Read 2026-09-07 from
#: https://docs.fwd.app/latest/application/nqe/language/data-model/type-vendor
#: , .../type-devicetype and .../type-os. Refresh from there if Forward adds a
#: value; the SDK gaining one is not evidence that NQE has.
NQE_MEMBERS: dict[str, frozenset[str]] = {
    # A block of names, kept as written on the page so it can be diffed against it.
    "Vendor": frozenset(
        """A10 ALKIRA AMAZON ARISTA ARUBA AVAYA AVI_NETWORKS AZURE BLUECAT BROCADE
        CHECKPOINT CISCO CITRIX CUMULUS DELL EDGE_CORE EXTREME F5 FORCEPOINT FORTINET
        FORWARD_CUSTOM GENERAL_DYNAMICS GOOGLE HP HUAWEI JUNIPER LINUX_GENERIC NOKIA
        PALO_ALTO_NETWORKS PENSANDO PICA8 RIVERBED SILVER_PEAK SYMANTEC T128 UNKNOWN
        VERSA VIASAT VMWARE ZSCALER""".split()  # noqa: SIM905
    ),
    "OS": frozenset(
        """ACOS ALKIRA_CXP APIC ARISTA_EOS ARUBA_AOS_CX ARUBA_SWITCH ARUBA_WIFI ASA AVAYA
        AVAYA_ERS AVI_VANTAGE BLUECAT BLUECOAT BROCADE_SWITCH CHECKPOINT CHECKPOINT_MANAGER
        CISCO_WIRELESS CLOUD_GENIX CLOUD_GENIX_PORTAL CUMULUS DELL_OS10 DELL_OS6 DELL_OS9
        DELL_SONIC EDGE_CORE_SONIC ENCRYPTOR ENCS_NFV ESXI EXTREME_NOS F5 F5_OS_HYPERVISOR
        FORCEPOINT FORTINET FXOS GENERAL_DYNAMICS_ENCRYPTOR HP_COMWARE HP_PROVISION HUAWEI
        INTERNET_SERVICE INTRANET_SERVICE IOS IOS_XE IOS_XR JUNOS L2_VPN_SERVICE LINUX
        LINUX_OVS_OFCTL MERAKI_MR MERAKI_MS MERAKI_MX MIST_AP MPLS_VPN_SERVICE NETSCALER
        NETSCREEN NOKIA NXOS OTHER PAN_OS PENSANDO PICA8_OVS_OFCTL SG SILVER_PEAK
        SILVER_PEAK_ORCHESTRATOR SRX T128 T128_CONDUCTOR UNKNOWN VELOCLOUD_EDGE
        VELOCLOUD_GATEWAY VERSA VIASAT_ENCRYPTOR VIPTELA ZSCALER_APP_CONNECTOR
        ZSCALER_ZPA""".split()  # noqa: SIM905
    ),
    "DeviceType": frozenset(
        """CONTROLLER ENCRYPTOR FIREWALL INTERNET INTRANET L2_VPN L3_VPN LOAD_BALANCER
        MISSING_PEER OTHER ROUTER SD_WAN SWITCH WAN_CIRCUIT WAN_OPTIMIZER
        WIFI_ACCESS_POINT""".split()  # noqa: SIM905
    ),
}

#: Where each list was read from, so a refresher knows what to open.
MEMBER_SOURCES: dict[str, str] = {
    "Vendor": "https://docs.fwd.app/latest/application/nqe/language/data-model/type-vendor",
    "DeviceType": (
        "https://docs.fwd.app/latest/application/nqe/language/data-model/type-devicetype"
    ),
    "OS": "https://docs.fwd.app/latest/application/nqe/language/data-model/type-os",
}


def nqe_members(enum_type: str) -> frozenset[str]:
    """The member names NQE accepts for a type."""
    return NQE_MEMBERS[enum_type]


VENDOR = "Vendor"
DEVICE_TYPE = "DeviceType"
OS = "OS"

#: The ``Type.`` form, as it appears inside a query's string literals.
KNOWN_PREFIXES: frozenset[str] = frozenset(f"{name}." for name in TYPE_SOURCES)

# What else Forward can give Infrahub

An audit of Forward's data and Infrahub's schema library: what is synced, what
could be, and what has nowhere to go yet. Each claim says how it is known.

- **Measured**: run against a live Forward network of 122 devices.
- **Source**: read from the Python SDK, Forward's NQE data model, or Infrahub's
  schema library.
- **Reported**: stated by the Forward SDK maintainers, not checked here.
- **Inferred**: a judgement, not checked.

## Synced today

Devices, interfaces (physical, virtual, LAG, layer-two membership), sites,
platforms, device types, manufacturers, tags, VRFs and IP namespaces, VLANs,
prefixes, IP addresses, cables, the cloud inventory, and, new, **OS releases
with their end-of-support dates** and **confirmed CVEs with the devices they
affect**.

## New: lifecycle and vulnerabilities

Infrahub's schema library has no place for either. A case-insensitive search of
its `base`, `extensions` and `experimental` trees finds no CVE, no end-of-life
or end-of-support field, no device software version and no device warranty
(**source**). `DcimPlatform` is a name and a description; the only version
fields are on an InfiniBand switch and a routing engine. So this package ships
two small node kinds, in the same extension file as the Cloud kinds, and a
relationship from each to `DcimDevice`:

| Kind | One row per | Carries |
| --- | --- | --- |
| `DcimSoftwareVersion` | OS family and version | the end of maintenance, of vulnerability support and of support, the vendor's announcement, its platform and the devices running it |
| `SecurityVulnerability` | CVE confirmed on a device | severity, publication date, CISA known-exploited flag, advisory URL and the devices it affects |

On the 122-device network (**measured**):

- 16 OS releases, 10 with support dates. 79 devices have a date, and 74 of them
  are past end of support.
- 376 confirmed CVEs on 119 devices: 24 critical, 147 high, 183 medium, 22 low,
  22 on CISA's known-exploited list, 69 devices with a critical one. Forward
  lists 5,746 findings in all, but most are CVEs it ruled out on the device's
  configuration (`isVulnerable` is false); only the confirmed ones are synced.

Both tables are optional. They are separate queries so that a licence tier
without the data never touches the device sync.

### Licence tiers

Forward restricts some NQE data by the organization's licence tier, and does so
quietly (**reported**): the restricted paths `cveFindings`, `cveDatabase`,
`osSupport` and `components[].support` come back **empty with no error**. Only a
whole query outside the tier is refused, with a 403. The API does not report the
tier. So an empty vulnerability table is ambiguous, and the sync warns when a
network of ten or more devices returns no confirmed CVE at all. `device.securityPolicy`
is a further case (**reported**): it exists only when an organization setting for
PAN-OS or FortiOS rules is on, off by default, and then only for those two vendors.

### Design notes

- A device is linked to the release it runs **from the device**, not from the
  release. A device has one release, and Infrahub refuses to give it a second: with
  the link written from the release side, a device that moved to a new release made
  the apply fail and the new release was never created (**measured**, on Infrahub
  1.12.2). Written from the device it is simply repointed. The price is that the
  release mapping, its place in `order` and the device's `software_version` field are
  removed together.
- A CVE's devices are written from the CVE side, as the relationship is many to many.
  Two syncs with different device scopes that share a CVE would each write only their
  own devices, and may never settle; one sync per network avoids it. Not tested.
- infrahub-sync v3 never deletes, so a release that no device runs any more, and a
  CVE that is no longer found, stay in Infrahub (a CVE still linked to the devices it
  was found on) until someone removes them. The plan lists them as recorded deletes.

## Candidates, ranked

Value is for an Infrahub source of truth; feasibility is how directly Forward's
data fits a schema that already exists. Neither is measured.

| # | Candidate | Forward has (source) | Infrahub has (source) | Feasibility | Value |
| --- | --- | --- | --- | --- | --- |
| 1 | **Freshness stamp** | `snapshotInfo.collectionTime` per device | no field; one attribute on `DcimDevice` | high | medium: every record says when it was last seen |
| 2 | **BGP sessions and ASNs** | `bgp.neighbors`: peer device, local and peer AS, state, session type | `routing_bgp`: `RoutingAutonomousSystem`, `RoutingBGPSession`; needs a VRF on every object | medium | high |
| 3 | **Hardware inventory and support** | `platform.components[]`: part type, id, serial, and `support` dates (Cisco, Checkpoint, F5, Fortinet, Palo Alto only) | `device_module`, `module_port`, `transceiver`, `device_module_psu` | medium | high: serials, line cards, optics and their end of support |
| 4 | **OSPF, VRRP, MLAG** | OSPF areas and neighbours, FHRP groups, `ha.mlagPeer` and vPC domain | `routing_ospf`, `vrrp`, `mlag`; OSPF interfaces are keyed on a description alone | medium | medium |
| 5 | **Drift report** | everything already synced | the plan already records what Infrahub holds that Forward did not report | high | medium: v3 never deletes, so a list of orphans, such as a CVE since patched everywhere, is what an operator needs |
| 6 | **Forward Predict as a Proposed Change check** | `change_reports.compare` gives a `regression`, `no_regression` or `incomplete` verdict for a predicted change, with a Markdown rendering for a comment | Proposed Changes with Infrahub checks | low to medium: needs a way to turn an Infrahub branch into the configuration change Forward predicts | high, and the only item no CMDB sync can offer |
| 7 | **Checks and scorecards** | network checks and their pass or fail, scorecards | no home; as data or as checks on a Proposed Change | medium | medium |
| 8 | **Security policy and ACLs** | `aclEntries` broadly; `securityPolicy` flag-gated and PAN-OS and FortiOS only | `experimental/security`: zone-based and firewall-shaped, so a router ACL needs synthetic zones | low | medium |
| 9 | **The other direction** | the SDK writes locations, device tags, WAN circuits, L2 and L3 VPN objects | Infrahub as the intended state | low: needs a reverse adapter | high, and the largest piece of work |

Not worth syncing (**inferred**): SNMP communities (secrets), routing-policy
contents (the library's routing-policy kinds hold names only), and endpoint hosts
(not infrastructure records).

## Things worth knowing in the library

- `RoutingProtocol` requires both a device and a VRF, so even a default-VRF BGP
  or OSPF object needs an `IpamVRF`.
- `RoutingOSPFInterface` is identified by `description` alone; two interfaces
  with the same description collide.
- Several kinds have no human-friendly id (BGP sessions, modules, transceivers),
  so a keyed write needs an identifier this package would have to supply.
- `location_minimal` and `location_site` both define `LocationSite`; the example
  loads the latter.

## SDK gaps

Reported to the Python SDK maintainers and handled on its `main`, not yet
released: a documentation example that named a service that does not exist, the
licence-tier behaviour above (now documented, with the denial mapped to
`ForwardPermissionError`), and the NQE fields for these tables. The SDK has no
typed rows for CVE findings, support dates or hardware parts; they live in this
package until a second integration needs them.

# Changelog

## [Unreleased]

### Changed

- infrahub-sync locked at `feature/v3-develop` @ `d2a93d2` (3.0.0a5), from
  `7d545c3`. Upstream now sandboxes `schema_mapping` transforms, requires safe
  source watermarks and a complete baseline for incremental runs, requires both
  sides to be fully extracted before computing deletes, and requires the
  `INFRAHUB_SYNC_CREDENTIAL_` prefix on environment credentials for registered
  runs. The test suite passes against it (199); not yet run live.
- `infrahub-sdk[ctl]` added to the dev group. 3.0.0a4 moved `[ctl]` out of
  infrahub-sync's base dependencies into its `service` extra, so `infrahubctl`
  (used by `infrahub-schema-load.sh`) is no longer installed implicitly.
- infrahub-sync bumped to `feature/v3-develop` @ `7d545c3` (3.0.0a3), from
  `b16dcfe` (3.0.0a1). Upstream retired the private-render keyed-write gate
  (`UnkeyedWriteRefusedError`) that refused any create whose destination kind's
  human-friendly ID crosses a relationship -- `InterfacePhysical`,
  `InterfaceVirtual`, `IpamPrefix` and `IpamIPAddress` all have one. Writes are
  now keyed by a recorded destination id for updates and by proven HFID
  coverage for creates (ADR 0013), which this package's mappings already
  satisfy. Verified live against Infrahub 1.11.2: all four kinds create,
  update and re-plan to no operations.
- forward-sdk bumped to 0.1.15 from 0.1.5. Its paging guard used to compare
  each page's first and last row to detect a stalled server; a query whose
  rows look identical across pages (an aggregate, `select {n: 1}`) produced
  the same signature whether the offset advanced or not, misdiagnosing a
  query that was paging correctly as a server-side stall at exactly
  `repeat_limit(25) * page_size(10000) = 250,000` rows. That is the query
  shape this package's own probe used to "confirm" a 250,000-row Forward-side
  paging cap, recorded as project memory and built into `fetch.py` as a
  proactive early refusal. There never was such a cap; forward-sdk 0.1.6
  fixed the false positive, verified there against a 545,464-row result
  paging to completion in 55 pages. The proactive check is removed. The
  sharding fallback stays -- for a genuine row/page-count guard trip, not a
  hard-coded row count -- since a table can still legitimately outgrow the
  guards on a large enough network.

### Fixed

- A response body the Forward SDK could not parse no longer escapes the
  adapter's error handling. Every fetch funnels failures into
  `ForwardLoadError` with one `except ForwardError`, but until forward-sdk
  0.1.5 an unparseable body raised pydantic's `ValidationError`, which is not a
  `ForwardError` and walked straight through. The pin moves to 0.1.5, and a
  test drives the installed SDK to check that nothing it raises falls outside
  that hierarchy.
- `forward-infrahub` reports a Forward error as one line rather than a
  traceback through SDK internals. An expired password or an unreachable
  appliance is an operating condition, not a defect in this tool. `-v` still
  prints the traceback.

## [0.1.0a2] - 2026-09-07

### Fixed

- MAC addresses are sent in the case Infrahub stores them in. It upper-cases a
  MacAddress on write and Forward reports it lower case, so every physical
  interface differed from itself on the next run and was rewritten. Found on a
  real network, where it affected 1,986 interfaces.

## [0.1.0a1] - 2026-09-07

First alpha. Verified against Forward Networks 26.8.4 and Infrahub 1.11.2.

### Added

- Forward Networks source adapter for infrahub-sync, registered as
  `forwardnetworks`.
- Fifteen bundled NQE queries with tested column contracts, covering devices,
  interfaces, sites, platforms, device types, manufacturers, VRFs, VLANs,
  prefixes, IP addresses, cables and cloud inventory.
- A `Cloud*` schema extension for the cloud inventory the Infrahub schema
  library does not model.
- `forward-infrahub` command: `check-contracts`, `resolve-snapshot`,
  `publish-queries`, `audit-queries`.
- Incremental runs from Forward snapshot diffs, gated on published queries and
  an unchanged ownership scope.
- Offline replay from a JSON fixture, for tests and for trying the adapter
  without Forward credentials.

### Notes

- Depends on `forward-sdk==0.1.4` from PyPI, pinned exactly because the SDK is
  pre-1.0.
- Pins `infrahub-sdk<1.23`, where the class inheritance infrahub-sync's model
  generator depends on was removed. Without the pin every generated field is
  typed `str` and rejects its own value.
- `infrahub-sync` 2.0.0 also stringifies every attribute it reads back, which
  is fixed on their `main` but unreleased. See `upstream/`.

### Known limitations

- Needs `infrahub-sync` from `main`. Its released 2.0.0 stringifies every
  attribute it reads back, so a second sync cannot load its own destination.
  Fixed upstream on 2026-05-29 and not yet in a release. Run
  `forward-infrahub check-environment` to see whether your install has it.
- Cloud objects have been read from a real network, 1,149 accounts across AWS
  and Azure, but never written into Infrahub. The mapping is covered by tests
  against a fixture only.
- `publish-queries` has not been run against a live Forward library. Queries
  run inline until published, which works but rules out snapshot diffs.
- Incremental runs are implemented and unit tested, but no live run has yet
  taken the snapshot-diff path.
- Prefix syncing is impractical at scale: infrahub-sync writes one object per
  call, so the 83,775 IPv4 prefixes on one 37-device network would take about
  three hours.

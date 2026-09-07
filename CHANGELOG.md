# Changelog

## [Unreleased]

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

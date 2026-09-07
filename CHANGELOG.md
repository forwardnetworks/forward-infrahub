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

- Depends on `forward-sdk==0.1.1` from PyPI, pinned exactly because the SDK is
  pre-1.0.
- `infrahub-sync` 2.0.0 needs the two patches in `upstream/` before it can sync
  any attribute that is not Text.

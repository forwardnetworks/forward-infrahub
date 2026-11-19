# Changelog

## [Unreleased]

### Changed

- Verified against Infrahub 1.12.1 as well as 1.11.9, with the same results: the
  schema loads, identities agree, a fixture plan and apply are clean, and on a
  122-device Forward network a plan takes 10 seconds, an apply of 3,026 objects
  153, and a second plan finds nothing to do. A hand-deleted object and a
  hand-edited description were repaired by one plan and apply. infrahub-sdk is
  locked at 1.24.0 (from 1.22.3), and the tests and live runs above were repeated
  on it.

## [0.1.0a3] - 2026-11-18

### Added

- `scripts/verify_diffs.py`: for each bundled query, folds Forward's NQE diff
  between two snapshots into the rows of the first and checks the result equals a
  full fetch of the second. Run on real snapshots of a changing network, all 15
  tables reconstructed exactly in two pairs, one of them with most of the network
  changed.

### Changed

- Relicensed from MIT to the Functional Source License 1.1 with an Apache-2.0
  future license (`FSL-1.1-ALv2`); adds `NOTICE`. Commits up to and including
  `be85aab` carry the MIT License and stay under it.
- README rewritten for infrahub-sync v3: plan, review and apply, a no-Forward
  demo on the bundled fixture, a gallery of 24 screenshots (every kind it
  writes, a device's detail and interface pages, and the terminal workflow),
  and where this stands
  against v3 (custom adapters in its Sync service are not supported upstream
  yet, and 2.x is not supported).

- infrahub-sync locked at `feature/v3-develop` @ `87d5a47` (still 3.0.0a5), 172
  commits past `d2a93d2`: plan approval and verification, resumable applies,
  a v2-to-V3 configuration converter, per-record source and owner, and a Sync
  web frontend. forward-sdk bumped to 0.1.38 from 0.1.29. Run live on a Forward
  demo network of 122 devices; see the README's Verified section.
- Queries now go through `client.nqe.query` instead of `execute`/`wait`/`rows`
  by hand, with the new `outcome_retries` setting (default 2, 0 turns it off):
  a query Forward ran and finished with a transient `SYSTEM_ERROR` is
  resubmitted by the SDK. A timeout and a compile error are not retried. A
  paging stall is not something the SDK retries either, so the device-shard
  fallback stays. `timeout` now applies to each attempt, so a query that keeps
  failing with `SYSTEM_ERROR` can take up to three times it (plus backoff)
  before the load fails.
- forward-sdk bumped to 0.1.29 from 0.1.15. `snapshots.latest_processed_id()`,
  which `snapshot.py` uses to resolve the default snapshot, now follows
  Forward's own rule -- the newest processed snapshot by creation, skipping
  drafts and forks -- where before it ordered by `processedAt`, so a reprocessed
  older snapshot could be picked over a newer one. NQE queries gain opt-in
  outcome retries (used above) and `ForwardExecutionError` carries `detail`
  and `attempts`. Used live; see the README.
- infrahub-sync locked at `feature/v3-develop` @ `d2a93d2` (3.0.0a5), from
  `7d545c3`. Upstream now sandboxes `schema_mapping` transforms, requires safe
  source watermarks and a complete baseline for incremental runs, requires both
  sides to be fully extracted before computing deletes, and requires the
  `INFRAHUB_SYNC_CREDENTIAL_` prefix on environment credentials for registered
  runs. Run live on a Forward demo network; see the README's Verified section.
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

- Queries already published to a Forward library keep running the old text: the
  adapter prefers the library copy and does not compare it with the bundle at run
  time. After upgrading, run `forward-infrahub audit-queries` and then
  `publish-queries`. An old `forward_cables` fails on a published library with
  "Duplicate rows found".
- An interface with no MAC address was written to Infrahub as an empty string,
  which its `MacAddress` attribute rejects, so a real apply stopped part way
  through. The query now reports null and the transform leaves it null.
- A trunk interface was rewritten on every run: Forward lists a trunk's VLANs in
  the order it holds them, Infrahub reads the relationship back in its own, and
  a list that differs only in order is a change. Every many-relationship is now
  resolved in sorted order, which is how infrahub-sync reads them back, so a
  device's tags and a cable's two ends are covered as well as a trunk's VLANs.
  A second plan on a real network finds nothing to do.
- `forward_cables.nqe` emitted each link twice, once from each end. An inline
  query tolerated it, but a query published to Forward's library enforces its
  primary key and failed with "Duplicate rows found". It is `select distinct`
  now: 398 rows became 199 on the network this was found on.
- The device description carried the vendor's enum prefix ("Vendor.CISCO ...").
  Existing devices get a one-time description update on the next apply.
- A null MAC clears a MAC already stored in Infrahub, including one typed in by
  hand, the same as any other attribute this sync owns.
- A LAG number now ignores leading zeros (`Po01` is 1) and reads zero outside
  0 to 4095, and the digits are matched only for LAGs.
- `forward_interfaces.nqe` could not run on Forward at all. The layer-two and
  LAG work was written and tested only against the replay fixture, which serves
  rows by query name and never parses query text, so three errors reached the
  first live run: a bare `let x = foreach ...` (Forward's parser needs
  `let x = (foreach ...)`), field access on the `VlanIdOrRange` union
  (`entry.ID`, `entry.RANGE`; a union is read with `when entry is ID(i) -> ...;
  RANGE(r) -> ...`), and `toNumber` on a string (it takes an IP address; a LAG's
  number is now matched against the integers 0 to 4095, and anything outside
  reads as zero). All 15 bundled queries now run on a live network, and a plan
  against it completed. New tests keep the first two out and run Forward's
  offline `fwdctl nqe lint` over every bundled query when it is installed.
- Credentials were referenced by plain environment names (`FORWARD_USERNAME`,
  `INFRAHUB_API_TOKEN`), which infrahub-sync v3 now refuses outside its
  `INFRAHUB_SYNC_CREDENTIAL_` namespace, so every run failed at configuration
  check. The adapter now republishes them under that namespace for the run.
- `infrahub-schema-load.sh` did not load the `lag` extension the config maps
  (`InterfaceLag`), and its account snippet omitted the `password` Infrahub
  requires.
- The example README still described the v2 `generate`, `diff` and `sync`
  commands.
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

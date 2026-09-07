<h1>forward-infrahub</h1>

Sync [Forward Networks](https://www.forwardnetworks.com/) into
[Infrahub](https://github.com/opsmill/infrahub): a source adapter for
[infrahub-sync](https://docs.infrahub.app/sync/), in the shape of the IP Fabric
adapter it sits beside.

> Status: pre-release. Targets infrahub-sync 2.0 and the Infrahub schema library.

Forward collects the network by talking to it. Infrahub records what the
network is supposed to be. This puts the first into the second, so a device,
its interfaces, its addresses and the cables between them arrive in Infrahub
as collected rather than as typed in by hand.

## Install

```bash
pip install forward-infrahub
```

That registers the `forwardnetworks` adapter with infrahub-sync; no `adapter:`
line is needed in `config.yml`.

## Use it

Start from [`examples/forwardnetworks_to_infrahub`](examples/forwardnetworks_to_infrahub),
which carries a full `config.yml`, the schema to load, and the exact steps.
Briefly:

```bash
export FORWARD_URL=https://fwd.app FORWARD_USERNAME=... FORWARD_PASSWORD=...
export FORWARD_NETWORK_ID=...

infrahub-sync generate --name from-forwardnetworks --directory examples
infrahub-sync diff     --name from-forwardnetworks
infrahub-sync sync     --name from-forwardnetworks
```

`diff` writes nothing, so run it first on a network you have not synced before.

## What it syncs

Devices, interfaces, sites, platforms, device types and manufacturers; VRFs,
VLANs, prefixes and IP addresses; cables inferred from resolved links; and
cloud accounts, VPCs, subnets and services. Everything but the cloud kinds maps
onto the Infrahub schema library. Cloud has no library equivalent, so a small
`Cloud*` extension ships with the package.

Data comes from Forward's query language, NQE. Fifteen queries are bundled and
their columns are a tested contract, so an upgrade that changes one is caught
by a test rather than by a sync that quietly stops populating a field.

## Design notes worth knowing before you run it

**One snapshot per run.** The snapshot is resolved once and pinned. A snapshot
that finished processing halfway through a run would otherwise leave different
queries describing different networks. `snapshot_id: latestCollected` picks the
newest snapshot that actually collected something, which is not always the
newest processed one.

**Every VRF is an IP namespace.** Prefix and address uniqueness in Infrahub is
per namespace, and the same subnet in two VRFs is ordinary in collected data.
Mapping everything into one namespace fails on the second occurrence.

**Cloud identity is the provider's id.** Not the display name, which changes.
Renaming a VPC in a console must not create a second VPC in Infrahub.

**Deletes are off.** Forward reports what it collected, and a device that
failed collection looks exactly like one that was decommissioned. Read
[docs/safety.md](docs/safety.md) before turning them on.

## Commands

`infrahub-sync` runs the sync. `forward-infrahub` does the things around it:

```bash
forward-infrahub check-contracts                     # bundled queries are intact
forward-infrahub resolve-snapshot --config config.yml # which snapshot a run would use
forward-infrahub publish-queries  --config config.yml # publish, enabling snapshot diffs
forward-infrahub audit-queries    --config config.yml # library still matches the bundle
```

Publishing the queries is what makes [incremental runs](docs/incremental.md)
possible: Forward can only diff a query it has committed.

## Configuration

Credentials come from `FORWARD_URL`, `FORWARD_USERNAME`, `FORWARD_PASSWORD` and
`FORWARD_NETWORK_ID`, the same variables the Forward SDK and the NetBox and
Nautobot integrations use. Everything else lives under `source.settings` in
`config.yml`; the example lists each option with what it does.

To narrow which devices a sync owns, set `device_vendors`, `device_types` or
`device_models`. A scoped sync only ever creates and updates, never deletes,
because it speaks for part of the network rather than all of it.

## Development

```bash
uv sync
uv run pytest          # offline, against a fixture
uv run ruff check .
uv run mypy
```

The test suite needs neither Forward nor Infrahub: `replay_fixture` points the
adapter at a JSON file of rows, which is also the fastest way to see what the
adapter produces.

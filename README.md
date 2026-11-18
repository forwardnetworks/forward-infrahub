<h1>forward-infrahub</h1>

Sync [Forward Networks](https://www.forwardnetworks.com/) into
[Infrahub](https://github.com/opsmill/infrahub): a source adapter for
[infrahub-sync](https://github.com/opsmill/infrahub-sync) v3.

> **Status: alpha.** Targets the infrahub-sync v3 alpha, which is not on PyPI
> yet. Verified against Infrahub 1.11.9 and infrahub-sync `feature/v3-develop`
> @ `87d5a47`, on the bundled fixture and on a live Forward network (see
> [Verified](#verified)).

Forward collects the network by talking to it. Infrahub records what the
network is supposed to be. This puts the first into the second, so a device,
its interfaces, its LAGs, its addresses and the cables between them arrive in
Infrahub as collected rather than as typed in by hand.

![Devices synced into Infrahub](docs/images/infrahub-devices.png)

## What you get

- **A reviewable plan before anything is written.** `plan` works out what a sync
  would do and saves it with a checksum; `review` proves the saved plan is the
  one you read; `apply` writes it only if the checksum you pass still matches.
- **Devices, interfaces, LAGs, addresses and cables.** Sites, platforms, device
  types and manufacturers; VRFs (each its own IP namespace), VLANs, prefixes and
  IP addresses; LAG membership and layer-two VLAN membership; cables inferred
  from resolved links; tags Forward carries; and cloud accounts, VPCs, subnets
  and services.
- **A tested query contract.** Data comes from Forward's query language, NQE.
  Fifteen queries are bundled and their columns are checked by a test, so an
  upgrade that changes one is caught there and not by a sync that quietly stops
  populating a field.
- **Incremental runs.** Once the queries are published, later runs read only
  what changed between two snapshots. See [docs/incremental.md](docs/incremental.md).
- **No deletes**, and guards for incomplete snapshots and scoped runs. See
  [docs/safety.md](docs/safety.md).

## Verified

On a live Forward demo network of 122 devices (snapshot reporting collection
failures, so the incomplete-snapshot gate was exercised), into Infrahub 1.11.9:

| | |
| --- | --- |
| Bundled queries run on Forward | all 15 |
| `plan` | about 3,000 creates, in 10 seconds |
| `apply` | 3,026 objects written in 157 seconds |
| `plan` again | no creates and no updates |
| Rows skipped as unplaceable | 0 |

Reading snapshot diffs, on real snapshots of a second network that had changed
between them (device count 133 to 698 in one pair, 83,747 prefixes down to 6,746;
a smaller pair with 5 changed interfaces and `+8/-46` prefixes): for every
bundled query, the rows of the first snapshot with Forward's NQE diff folded in
equal a full fetch of the second, exactly, in all 15 tables.
[`scripts/verify_diffs.py`](scripts/verify_diffs.py) runs that check on any
network. Separately, a second plan on an unchanged snapshot reported "the
baseline; nothing to read from Forward".

Not yet verified: the whole baseline handoff across two *different* snapshots.
The adapter never makes an incomplete snapshot a baseline, and none of the
account's networks had two complete snapshots, so that step was not exercised. Also
not verified: a network with many LAGs (the demo network has one), and Infrahub
versions other than 1.11.9.

## Install

infrahub-sync v3 is unreleased, so it comes from its git branch. Python 3.10 to
3.13.

```bash
pip install \
  "infrahub-sync @ git+https://github.com/opsmill/infrahub-sync@feature/v3-develop" \
  "forward-infrahub @ git+https://github.com/forwardnetworks/forward-infrahub"
```

From a checkout of this repo, `uv sync` does the same (the branch is declared
under `[tool.uv.sources]`), and its dev group carries `infrahubctl` for loading
the schema.

The package registers a `forwardnetworks` adapter with infrahub-sync, so
`config.yml` needs no `adapter:` line.

## Try it without Forward

The bundled fixture is a small, fake network: two devices, a LAG, a cable, a VLAN,
prefixes, and an AWS account. It needs no Forward credentials, and it is how the
screenshots on this page were made. You need an Infrahub to sync into; a
throwaway one is fine (verified on 1.11.9).

```bash
# 1. Load the schema this sync targets: the schema library plus a small Cloud* extension.
git clone https://github.com/opsmill/schema-library
export INFRAHUB_ADDRESS=http://localhost:8000 INFRAHUB_API_TOKEN=...
SCHEMA_LIBRARY=./schema-library ./examples/forwardnetworks_to_infrahub/infrahub-schema-load.sh

# 2. Create the account the destination attributes everything it writes to.
cat > account.yml <<'EOF'
---
apiVersion: infrahub.app/v1
kind: Object
spec:
  kind: CoreAccount
  data:
    - name: forwardnetworks
      label: Forward Networks
      account_type: Script
      password: pick-something-long
EOF
infrahubctl object load account.yml

# 3. Point the example config at the fixture.
mkdir -p demo/from-forwardnetworks
sed 's#^    url: "https://fwd.app"#    replay_fixture: tests/fixtures/forward_rows.json#; s#<NETWORK-ID>#demo#' \
  examples/forwardnetworks_to_infrahub/config.yml > demo/from-forwardnetworks/config.yml
```

Then plan, review and apply:

```console
$ forward-infrahub plan --config demo/from-forwardnetworks/config.yml --name from-forwardnetworks
run:      20261118T1815-027110ed
checksum: 590d8c63a8cf638981edea721131c482fc8dfba174e6c994692b69482bcef672
verified: yes
   create: 28
  BuiltinTag                 3
  CloudAccount               1
  ...

$ forward-infrahub apply 20261118T1815-027110ed --config ... --name from-forwardnetworks \
    --expected-checksum 590d8c63a8cf638981edea721131c482fc8dfba174e6c994692b69482bcef672
status: applied
   create: 28
   delete: 0
   update: 0
```

Planning again immediately reports no operations: a sync that has nothing to do
says so. In Infrahub the result is ordinary objects, linked the way the schema
says they should be:

| | |
| --- | --- |
| ![Interfaces](docs/images/infrahub-interfaces.png) | ![LAGs](docs/images/infrahub-lags.png) |
| ![Prefixes](docs/images/infrahub-prefixes.png) | ![Cables](docs/images/infrahub-cables.png) |

## Run it against Forward

```bash
export FORWARD_URL=https://fwd.app FORWARD_USERNAME=... FORWARD_PASSWORD=...
export FORWARD_NETWORK_ID=...
export INFRAHUB_ADDRESS=http://localhost:8000 INFRAHUB_API_TOKEN=...

# plan, review and apply expect <directory>/<name>/config.yml
mkdir -p sync/from-forwardnetworks
cp examples/forwardnetworks_to_infrahub/config.yml sync/from-forwardnetworks/config.yml
CFG=sync/from-forwardnetworks/config.yml

forward-infrahub check-contracts                              # bundled queries are intact
forward-infrahub resolve-snapshot --config $CFG               # which snapshot a run would use
forward-infrahub check-identities --config $CFG               # mapping and Infrahub agree on identity
forward-infrahub plan  --config $CFG --name from-forwardnetworks
forward-infrahub apply RUN_ID --config $CFG --name from-forwardnetworks \
    --expected-checksum CHECKSUM
```

Run `plan` and read it before the first `apply` on a network you have not
synced. [examples/forwardnetworks_to_infrahub](examples/forwardnetworks_to_infrahub)
has the full `config.yml`, the schema, and the load steps.

## Screenshots

Every kind this sync writes, as it appears in Infrahub after the fixture demo
above, and the terminal side of the workflow. The Infrahub pages and the first
three terminal images are of the bundled fixture, a small fake network. The diff
verification image is real output from a Forward demo network, with the network
and snapshot ids left out.

### The workflow

| | |
| --- | --- |
| ![Plan and review](docs/images/terminal-plan.png) | ![Apply, then plan again](docs/images/terminal-apply.png) |
| ![Checks before a sync](docs/images/terminal-checks.png) | ![Diff verification](docs/images/terminal-verify-diffs.png) |

### Devices and interfaces

A device's detail page shows its location, platform and type, and the activity
log records the Loopback with no MAC address as empty rather than as an invalid
value.

| | |
| --- | --- |
| ![Devices](docs/images/infrahub-devices.png) | ![A device](docs/images/infrahub-device-detail.png) |
| ![A device's interfaces](docs/images/infrahub-device-interfaces.png) | ![Physical interfaces](docs/images/infrahub-interfaces.png) |
| ![LAGs](docs/images/infrahub-lags.png) | ![Virtual interfaces](docs/images/infrahub-virtual-interfaces.png) |

### Addressing and links

| | |
| --- | --- |
| ![Prefixes](docs/images/infrahub-prefixes.png) | ![IP addresses](docs/images/infrahub-ip-addresses.png) |
| ![VLANs](docs/images/infrahub-vlans.png) | ![VRFs](docs/images/infrahub-vrfs.png) |
| ![Cables](docs/images/infrahub-cables.png) |  |

### Sites, platforms and tags

| | |
| --- | --- |
| ![Sites](docs/images/infrahub-sites.png) | ![Tags](docs/images/infrahub-tags.png) |
| ![Platforms](docs/images/infrahub-platforms.png) | ![Device types](docs/images/infrahub-device-types.png) |
| ![Manufacturers](docs/images/infrahub-manufacturers.png) |  |

### Cloud

| | |
| --- | --- |
| ![Cloud accounts](docs/images/infrahub-cloud-accounts.png) | ![VPCs](docs/images/infrahub-cloud-vpcs.png) |
| ![Subnets](docs/images/infrahub-cloud-subnets.png) | ![Services](docs/images/infrahub-cloud-services.png) |

## Commands

| Command | What it does |
| --- | --- |
| `plan` | Work out what a sync would write, save the plan and print its checksum. Writes nothing. |
| `review RUN_ID` | Re-read a saved plan and prove it is the one that was planned. `--detail` lists every operation. |
| `apply RUN_ID` | Write a reviewed plan to Infrahub. Needs `--expected-checksum`. |
| `models` | Show the models built from the destination schema and the identifiers each uses. |
| `check-contracts` | Verify the bundled queries still return the columns the mapping reads. |
| `check-identities` | Compare how the mapping identifies each kind with how Infrahub does. |
| `resolve-snapshot` | Show which snapshot a run would use, and whether it is complete. |
| `publish-queries` | Publish the bundled queries to Forward's library, which is what enables snapshot diffs. |
| `audit-queries` | Check the library still matches what this package ships. |

infrahub-sync v3's own `infrahub-sync` command is a client for its Sync service.
`plan`, `review` and `apply` here drive the same engine through its supported
Python API, so a sync can be run from one machine against a `config.yml`.

## Design notes worth knowing before you run it

**One snapshot per run.** The snapshot is resolved once and pinned. A snapshot
that finished processing halfway through a run would otherwise leave different
queries describing different networks. `snapshot_id: latestCollected` picks the
newest snapshot that actually collected something, which is not always the
newest processed one.

**Every VRF is an IP namespace.** Prefix and address uniqueness in Infrahub is
per namespace, and the same subnet in two VRFs is ordinary in collected data.
Mapping everything into one namespace fails on the second occurrence.

**Cloud identity is the provider's id**, not the display name, which changes.
Renaming a VPC in a console must not create a second VPC in Infrahub.

**Rows that cannot be placed are reported, not guessed at.** A row that names a
device Forward did not collect is skipped with a warning, and the plan says how
many.

**Deletes are never applied.** Forward reports what it collected, and a device
that failed collection looks exactly like one that was decommissioned.
infrahub-sync v3 records what Forward did not report and leaves it in Infrahub.
Read [docs/safety.md](docs/safety.md).

**Queries run on Forward's asynchronous NQE API.** Each is submitted, waited on and
paged to completion, and once published, an incremental run asks Forward for the
diff between two snapshots instead of the whole table.

**Transient query failures are retried by the SDK.** A query Forward ran and
finished with a transient error is resubmitted (`outcome_retries`, default 2). A
result too large to page falls back to fetching in device shards.

## Configuration

Credentials come from `FORWARD_URL`, `FORWARD_USERNAME`, `FORWARD_PASSWORD` and
`FORWARD_NETWORK_ID`, the same variables the Forward SDK and the NetBox and
Nautobot integrations use, and `INFRAHUB_API_TOKEN` for Infrahub. The Infrahub
address is `destination.settings.url` in `config.yml`. infrahub-sync
only reads a credential from its own `INFRAHUB_SYNC_CREDENTIAL_` namespace, so
the adapter republishes the ones you export under that name for the length of the
run. Everything else lives under `source.settings` in `config.yml`; the example
lists each option with what it does.

To narrow which devices a sync owns, set `device_vendors`, `device_types` or
`device_models`. A scoped sync only ever creates and updates, never deletes,
because it speaks for part of the network rather than all of it.

## Where this stands against infrahub-sync v3

- **Custom adapters in the Sync service are not supported upstream yet.** v3's
  Sync service and web interface run the adapters that ship with it, and refuse
  any other (upstream tracks this as INFP-654). This package runs through the
  engine's Python API instead, which is what the commands above do.
- **This package adds itself to infrahub-sync's adapter capability table at
  runtime** (`forward_infrahub/capabilities.py`), because that table is closed to
  adapters it does not ship. It does so only to pass v3's configuration checks
  for the engine path above; it is the thing to replace when INFP-654 lands.
- **infrahub-sync 2.x is not supported.** The first 2.1.0 sync works, but reading
  LAG membership back from Infrahub fails there, so a second run cannot diff.

## Development

```bash
uv sync
uv run pytest          # offline, against a fixture
uv run ruff check .
uv run mypy
```

The Forward SDK is pinned to an exact version rather than a range. It is pre-1.0
and its surface is still moving, so a range would let a breaking change in
silently.

The test suite needs neither Forward nor Infrahub: `replay_fixture` points the
adapter at a JSON file of rows, which is also the fastest way to see what the
adapter produces.

## License

Functional Source License 1.1, Apache-2.0 future license
([LICENSE](LICENSE), `FSL-1.1-ALv2`). It is source-available, not open source:
you can read, run, modify and redistribute it for any purpose except a competing
commercial product or service, and each release converts to Apache-2.0 two years
after it is published. Commits up to and including `be85aab` carry
the MIT License and stay under it. See [NOTICE](NOTICE) for third-party software, which
keeps its own license.

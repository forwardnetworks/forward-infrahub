# Safety

Forward reports what it collected. That is not the same as what exists, and
every guard here follows from that one fact.

## Deletes are never applied

infrahub-sync v3 does not delete. A plan records the objects in Infrahub that this
sync does not produce, `plan` prints how many (`26 delete(s) are recorded and will
not be applied`), and `apply` writes the creates and updates and leaves those
objects alone. Removing something from Infrahub is a decision for whoever owns
that data, made in Infrahub.

That is the right default for this source: a device whose collection failed looks
exactly like a device that was decommissioned. When you read a plan, the recorded
deletes are a list of what Forward did not report, not a list of what is gone.

Before trusting what Forward did report, understand what the source is claiming:

- **Was the snapshot complete?** `forward-infrahub resolve-snapshot` prints
  whether collection or processing failed. If it did, rows are missing for
  reasons that have nothing to do with the network changing.
- **Is the sync scoped?** Device and cloud allowlists mean the sync speaks for
  a subset. Everything outside that subset is missing from its output by
  design, and must not be read as deleted.

## The snapshot completeness gate

A run on a snapshot that reports collection or processing failures still
proceeds, with a warning (`snapshot N is incomplete; the baseline stays at the
last complete one`), and an incomplete snapshot never becomes the incremental
baseline. To refuse such a run outright, set `require_complete_snapshot: true`.
Metrics that cannot be read count as incomplete: an unverified snapshot is not a
clean one.

## Scoped runs

Any device or cloud allowlist makes the run partial. Two consequences:

- Tables with no device relation, notably prefixes, cannot be scoped at all: a
  route table does not say which device contributed a prefix. The adapter fails
  closed and writes nothing for them, and logs that it did.
- The scope is part of the fingerprint, so changing an allowlist invalidates
  the incremental baseline and forces a full load. See [incremental](incremental.md).

## Rows that cannot be placed

A row naming a peer that was not loaded is skipped and counted, not written
with a missing relationship. An interface whose device is out of scope, an
address whose interface never appeared: those are incomplete records, and a
partial write would be harder to notice than an absent one. The counts are
logged at the end of a load.

A table that fails abandons the whole run. A partial load is indistinguishable
from an upstream deletion, which is the one thing this design refuses to guess
about.

## Identity, and the failure that reports success

A sync's identifiers decide which source rows are the same object. Infrahub
resolves an upsert against a node's human-friendly id. When those disagree,
nothing fails: rows Infrahub considers one node are written one after another,
each overwriting the last, and every write is reported as a success.

That is how a mapping loaded 127 VLANs and stored 57. `IpamVLAN` inherits an id
of name alone, so VLANs sharing a name merged however they were scoped. The run
was clean and the only symptom was a count nobody had a reason to check.

```bash
forward-infrahub check-identities --config config.yml
```

Run it after any change to `identifiers`, to a transform feeding a field in an
id, or to the destination schema. It reads the live schema, so it answers for
the Infrahub you are actually syncing into.

## Publishing queries

A sync never writes to Forward's query library. `forward-infrahub
publish-queries` does, as a deliberate command, and validates the queries
against a real snapshot before committing so a query that no longer compiles is
caught before anyone else opens the library.

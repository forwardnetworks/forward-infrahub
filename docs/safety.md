# Safety

Forward reports what it collected. That is not the same as what exists, and
every guard here follows from that one fact.

## Deletes are off by default

The example config sets `SKIP_UNMATCHED_DST`, so a node in Infrahub that this
sync does not produce is left alone. Remove it only when this sync genuinely
owns those kinds in Infrahub, because the failure mode is not subtle: a device
whose collection failed looks exactly like a device that was decommissioned.

Before removing it, understand what the source is claiming:

- **Was the snapshot complete?** `forward-infrahub resolve-snapshot` prints the
  collection and processing failure counts. If they are non-zero, rows are
  missing for reasons that have nothing to do with the network changing.
- **Is the sync scoped?** Device and cloud allowlists mean the sync speaks for
  a subset. Everything outside that subset is missing from its output by
  design, and must not be read as deleted.

## The snapshot completeness gate

If the snapshot reports collection or processing failures and the config would
delete, the run refuses to start. Two ways past it:

- `allow_incomplete_snapshot_deletes: true` accepts the risk for a run.
- `require_complete_snapshot: true` goes the other way and refuses any run on
  an incomplete snapshot, even one that only creates.

Metrics that cannot be read count as incomplete. An unverified snapshot is not
a clean one.

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

## The row-count guardrail, and narrowing a sync on purpose

infrahub-sync compares each kind's row count against the last successful run
and refuses to proceed when one collapses, which is the right default: a source
that suddenly returns a fraction of what it did is far more often broken than
newly tidy.

It fires on a deliberate narrowing too. Adding the `scope_devices` filter to
`LocationSite` took a real network from 394 sites to 13 and stopped the run:

```
RowcountGuardrailError: Rowcount guardrail tripped for 'LocationSite':
dropped from 394 to 13 (ratio 0.03 < threshold 0.50).
Pass --allow-rowcount-drop to override.
```

Pass `--allow-rowcount-drop` once, having checked the drop is the one you
intended. The same applies the first time you add a device or cloud allowlist.

Note what the flag does not do. With deletes off, the objects from the wider
run stay in Infrahub: the 381 sites that fell out of scope are no longer
written, and nothing removes them either. They are orphans of a scope change,
not of anything upstream, and removing them is a decision for whoever owns that
data.

## Publishing queries

A sync never writes to Forward's query library. `forward-infrahub
publish-queries` does, as a deliberate command, and validates the queries
against a real snapshot before committing so a query that no longer compiles is
caught before anyone else opens the library.

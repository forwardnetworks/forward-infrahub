# Incremental runs

A full run reads every device in the snapshot. Forward can instead report what
changed between two snapshots of a committed query, which is much less work on
a large network. This is how that maps onto infrahub-sync's incremental
machinery.

## What has to be true

infrahub-sync decides per run whether to reuse the previous run's snapshot.
This adapter narrows that further, and a run reads diffs only when all of the
following hold:

1. **Every query is published.** Forward diffs a *committed* query; it has
   nothing to compare inline source against. Publish them with
   `forward-infrahub publish-queries`.
2. **A baseline exists.** After a clean run the adapter writes
   `baseline.json` in the sync's cache directory (`.infrahub-sync-cache/<sync name>/`), naming the snapshot it read,
   the ownership scope, and the commit of each query.
3. **The scope has not moved.** Different devices in scope, or an edited
   `schema_mapping`, means the previous rows were a different set.
4. **No query was republished.** New query source answers a different question,
   so old rows are not a baseline for it.

Anything else loads in full and logs the reason. `full_resync_every` in the
config forces a periodic full load regardless, which is worth keeping.

## Checking it against your own network

An incremental run is only right if the diff it reads, folded into the previous
rows, gives what a full read of the new snapshot would. Check that directly on
any two processed snapshots, with the bundle published:

```bash
uv run python scripts/verify_diffs.py NETWORK_ID SNAPSHOT_A SNAPSHOT_B
```

It reads only. For each query it fetches every row at A, applies Forward's NQE
diff from A to B with the same `apply_diff` the adapter uses, and compares with a
full fetch of B. It was run this way on real snapshots, including one pair where
most of the network changed, and every table reconstructed exactly.

## When a diff fails

A table whose diff cannot be read is fetched in full, with a warning that names
the table and both snapshots. A whole table read fresh is always right; a diff
that failed is not. Nothing is guessed from a partial delta.

## Deletions

The diff reports added, changed and deleted rows, and all three are applied to
the cached table, which is then handed to the sync whole. A row Forward stops
reporting is therefore absent from that run. Because infrahub-sync v3 never
deletes, the matching object stays in Infrahub; the plan lists it as a recorded
delete. See [safety](safety.md).

## Turning it off

```yaml
source:
  settings:
    incremental: false
```

Worth doing while the mapping is still changing: full runs are slower but have
no baseline to be wrong about.

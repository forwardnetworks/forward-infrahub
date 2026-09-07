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
   `.infrahub-sync-cache/forward-baseline.json`, naming the snapshot it read,
   the ownership scope, and the commit of each query.
3. **The scope has not moved.** Different devices in scope, or an edited
   `schema_mapping`, means the previous rows were a different set.
4. **No query was republished.** New query source answers a different question,
   so old rows are not a baseline for it.

Anything else loads in full and logs the reason. `full_resync_every` in the
config forces a periodic full load regardless, which is worth keeping.

## What it will not do

A failed diff is never quietly retried as a full load. A silent fallback
produces a delta that looks exactly like a real one while being computed from a
different baseline, and nothing downstream can tell the difference. The run
fails and says what went wrong.

## Deletions

The diff reports added, changed and deleted rows. This adapter yields added and
changed rows only. infrahub-sync detects removals by comparing what a run
reports against the previous run's cached snapshot, so a row that stops being
mentioned is already handled there, and reporting it twice would double count.

## Turning it off

```yaml
source:
  settings:
    incremental: false
```

Worth doing while the mapping is still changing: full runs are slower but have
no baseline to be wrong about.

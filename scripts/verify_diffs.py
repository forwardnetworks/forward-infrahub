"""Check that reading snapshot diffs gives the same rows as reading the whole snapshot.

For each bundled query: fetch every row at snapshot A, fold in Forward's NQE diff
from A to B with the adapter's own ``apply_diff``, and compare with a full fetch of
B. Any table that does not reconstruct exactly means an incremental run would write
something a full run would not.

Needs the bundle published (``forward-infrahub publish-queries``), because Forward
diffs a committed query. Reads only; it changes nothing in Forward or Infrahub.

    FORWARD_URL=https://fwd.app FORWARD_USERNAME=... FORWARD_PASSWORD=... \\
        uv run python scripts/verify_diffs.py NETWORK_ID SNAPSHOT_A SNAPSHOT_B
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from forward_sdk import ForwardClient, ForwardError
from forward_sdk.nqe import QueryRef

from forward_infrahub.queries import QUERY_FILENAMES, query_primary_key, query_stem
from forward_infrahub.rowcache import apply_diff
from forward_infrahub.settings import AdapterSettings


def canonical(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    network, before, after = argv[1:4]
    directory = AdapterSettings().query_directory.rstrip("/")
    client = ForwardClient(
        os.environ["FORWARD_URL"],
        username=os.environ["FORWARD_USERNAME"],
        password=os.environ["FORWARD_PASSWORD"],
        network_id=network,
        timeout=900,
    )
    problems = 0
    print(f"network {network}: snapshot {before} -> {after}")
    with client:
        for filename in QUERY_FILENAMES:
            stem = query_stem(filename)
            ref = QueryRef.by_path(f"{directory}/{stem}")
            started = time.time()
            try:
                rows_before = client.nqe.query(ref, network_id=network, snapshot_id=before)
                rows_after = client.nqe.query(ref, network_id=network, snapshot_id=after)
                entries = client.nqe.diff(ref, before=before, after=after)
            except ForwardError as exc:
                print(f"  ERROR {stem:24} {str(exc)[:100]!r}")
                problems += 1
                continue
            outcome = apply_diff(rows_before, entries, query_primary_key(filename))
            same = canonical(outcome.rows) == canonical(rows_after)
            problems += not same
            print(
                f"  {'ok   ' if same else 'DIFFERS'} {stem:24} {len(rows_before):6} -> "
                f"{len(rows_after):6}  +{outcome.added} -{outcome.deleted} ~{outcome.modified}"
                f"  {time.time() - started:4.0f}s"
            )
    print("every table reconstructs exactly" if not problems else f"{problems} table(s) differ")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

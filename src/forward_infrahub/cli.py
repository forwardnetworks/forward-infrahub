"""``forward-infrahub``: the commands around a sync, not the sync itself.

Running a sync is ``infrahub-sync``. These are the things an operator does
before or beside one: publish the bundled queries so snapshot diffs work, check
that what is published still matches what this package ships, see which
snapshot a run would pick, and check the query contracts.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import yaml
from forward_sdk import ForwardError

from forward_infrahub import __version__
from forward_infrahub.contracts import check_contracts
from forward_infrahub.environment import check_environment
from forward_infrahub.identity import check_identities
from forward_infrahub.publish import MATCHED, audit, publish
from forward_infrahub.queries import all_query_sources
from forward_infrahub.settings import AdapterSettings, build_client
from forward_infrahub.snapshot import resolve_snapshot

logger = logging.getLogger("forward_infrahub")


def load_settings(config_path: Path | None) -> AdapterSettings:
    """Settings from a sync config's source block, or from the environment."""
    if config_path is None:
        return AdapterSettings()
    payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    source = payload.get("source") or {}
    if source.get("name") not in (None, "forwardnetworks"):
        raise SystemExit(
            f"{config_path}: source.name is {source.get('name')!r}, not forwardnetworks"
        )
    return AdapterSettings.model_validate(source.get("settings") or {})


def _client_and_settings(args: argparse.Namespace) -> tuple[Any, AdapterSettings]:
    settings = load_settings(args.config)
    if args.query_directory:
        settings = settings.model_copy(update={"query_directory": args.query_directory})
    return build_client(settings), settings


def cmd_check_environment(args: argparse.Namespace) -> int:
    """Report the known problems with the installed infrahub-sync."""
    problems = check_environment()
    for problem in problems:
        print(problem)
        print()
    if problems:
        print(
            f"{len(problems)} problem(s) in infrahub-sync itself, not in this package. "
            "A sync fails on the first attribute that is not text until they are patched."
        )
        return 1
    print("infrahub-sync has neither of the problems this adapter knows about")
    return 0


def cmd_check_identities(args: argparse.Namespace) -> int:
    """Compare how the mapping identifies each kind with how Infrahub does."""
    payload = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    address = args.infrahub_address or os.environ.get("INFRAHUB_ADDRESS")
    if not address:
        address = ((payload.get("destination") or {}).get("settings") or {}).get("url")
    if not address:
        raise SystemExit("no Infrahub address: pass --infrahub-address or set INFRAHUB_ADDRESS")
    token = args.infrahub_token or os.environ.get("INFRAHUB_API_TOKEN", "")

    response = httpx.get(
        f"{address.rstrip('/')}/api/schema",
        params={"branch": args.branch},
        headers={"X-INFRAHUB-KEY": token} if token else {},
        timeout=60,
    )
    response.raise_for_status()
    checks = check_identities(response.json(), payload.get("schema_mapping") or [])
    risky = [c for c in checks if c.merges]
    for check in checks:
        if check.merges or args.verbose:
            print(check.summary)
    if risky:
        print(
            f"\n{len(risky)} of {len(checks)} kinds would have rows written over each other. "
            "This does not fail a sync; it loses data while reporting success."
        )
        return 1
    print(f"all {len(checks)} kinds identify the same way here and in Infrahub")
    return 0


def cmd_check_contracts(args: argparse.Namespace) -> int:
    problems = check_contracts(all_query_sources())
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"{len(all_query_sources())} bundled queries match their contracts")
    return 0


def cmd_resolve_snapshot(args: argparse.Namespace) -> int:
    client, settings = _client_and_settings(args)
    resolved = resolve_snapshot(client, settings, settings.resolved_network_id())
    print(f"snapshot:     {resolved.id}")
    print(f"selector:     {resolved.selector}")
    print(f"completeness: {resolved.completeness.status}")
    if resolved.completeness.reason:
        print(f"reason:       {resolved.completeness.reason}")
    return 0


def cmd_audit_queries(args: argparse.Namespace) -> int:
    client, settings = _client_and_settings(args)
    entries = audit(client, settings.query_directory)
    for entry in entries:
        print(f"{entry.status:18} {entry.path}{'  ' + entry.detail if entry.detail else ''}")
    unmatched = [e for e in entries if not e.ok]
    if unmatched:
        print(
            f"\n{len(unmatched)} of {len(entries)} queries are not published as bundled. "
            "Run `forward-infrahub publish-queries` to update the library."
        )
        return 1
    print(f"\nall {len(entries)} queries match the library")
    return 0


def cmd_publish_queries(args: argparse.Namespace) -> int:
    client, settings = _client_and_settings(args)
    snapshot_id = None
    if not args.no_dry_run:
        snapshot_id = resolve_snapshot(client, settings, settings.resolved_network_id()).id
        print(f"validating against snapshot {snapshot_id}")
    report = publish(client, settings.query_directory, snapshot_id=snapshot_id)
    changed = getattr(report, "changed", None)
    print(
        f"published to {settings.query_directory}" if changed is not False else "already up to date"
    )
    entries = audit(client, settings.query_directory)
    unmatched = [e for e in entries if e.status != MATCHED]
    if unmatched:
        for entry in unmatched:
            print(f"{entry.status:18} {entry.path}")
        return 1
    print(f"all {len(entries)} queries match the library")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forward-infrahub", description=__doc__)
    parser.add_argument("--version", action="version", version=f"forward-infrahub {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log what is happening")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler: Any, help_text: str, *, needs_forward: bool = True) -> None:
        sub = subparsers.add_parser(name, help=help_text)
        sub.set_defaults(handler=handler)
        if needs_forward:
            sub.add_argument(
                "--config",
                type=Path,
                help="a sync config.yml to read source.settings from "
                "(default: the FORWARD_* environment variables)",
            )
            sub.add_argument(
                "--query-directory", help="override the library directory from the config"
            )

    add("check-contracts", cmd_check_contracts, "verify the bundled queries", needs_forward=False)
    add(
        "check-environment",
        cmd_check_environment,
        "report known problems in the installed infrahub-sync",
        needs_forward=False,
    )
    add(
        "check-identities",
        cmd_check_identities,
        "compare the mapping's identifiers with Infrahub's own",
        needs_forward=False,
    )
    identities = subparsers.choices["check-identities"]
    identities.add_argument("--config", type=Path, required=True, help="the sync config.yml")
    identities.add_argument("--infrahub-address", help="default: INFRAHUB_ADDRESS, or the config")
    identities.add_argument("--infrahub-token", help="default: INFRAHUB_API_TOKEN")
    identities.add_argument("--branch", default="main")
    add("resolve-snapshot", cmd_resolve_snapshot, "show which snapshot a sync would use")
    add("audit-queries", cmd_audit_queries, "compare the library against the bundled queries")
    add("publish-queries", cmd_publish_queries, "publish the bundled queries to the library")
    publish_parser = subparsers.choices["publish-queries"]
    publish_parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="skip validating the queries against a snapshot before committing",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    handler: Any = args.handler
    try:
        result: int = handler(args)
    except ForwardError as exc:
        # An expired password or an unreachable appliance is an ordinary
        # operating condition, not a defect in this tool. Reporting it as a
        # traceback through SDK internals buries the one line that says what
        # to fix. -v keeps the traceback for when the cause is not obvious.
        logger.error("%s", exc)
        if args.verbose:
            logger.exception("full traceback")
        return 1
    return result


if __name__ == "__main__":
    sys.exit(main())

"""``forward-infrahub``: running a sync, and the things done around one.

infrahub-sync v3 runs a sync through a service, and its own command line is an
HTTP client for one. ``models``, ``plan`` and ``apply`` here drive the same
engine through its supported Python API, so a sync can be run from one machine
against a ``config.yml``. The rest are the things done around a sync: publish
the bundled queries so snapshot diffs work, check that what is published still
matches what this package ships, see which snapshot a run would pick, and check
the query contracts.
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

from forward_infrahub.composition_root import build_client, forward_port, query_catalogue
from forward_infrahub.ports import (
    QUERY_FILENAMES,
    ForwardPort,
    __version__,
    check_contracts,
    check_identities,
)
from forward_infrahub.publish import MATCHED, audit, publish
from forward_infrahub.runtime import prepare
from forward_infrahub.settings import AdapterSettings
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


def _client_and_settings(args: argparse.Namespace) -> tuple[ForwardPort, AdapterSettings]:
    """The Forward port for the configured client, and the settings it was built from."""
    settings = load_settings(args.config)
    if args.query_directory:
        settings = settings.model_copy(update={"query_directory": args.query_directory})
    return forward_port(build_client(settings)), settings


def cmd_models(args: argparse.Namespace) -> int:
    """Show the models a run would build from the destination schema."""
    run = prepare(Path(args.config), args.name, args.branch)
    for kind in sorted(run.models):
        model = run.models[kind]
        print(f"{kind:26} identifiers={','.join(model._identifiers)}")
    print(f"\n{len(run.models)} kind(s) built from the schema on branch {args.branch!r}")
    return 0


def _print_plan(plan: Any) -> None:
    """What a reviewer needs: the counts, the checksum, and what will not happen."""
    summary = plan.summary()
    print(f"run:      {plan.manifest.run_id}")
    print(f"checksum: {plan.manifest.plan_checksum}")
    print(f"verified: {'yes' if plan.checksum_ok else 'NO'}")
    for note in plan.verification_notes:
        print(f"  note: {note}")
    for action, count in sorted(summary.by_action.items()):
        print(f"{action:>9}: {count}")
    for kind, count in sorted(summary.by_kind.items()):
        print(f"  {kind:26} {count}")
    if summary.deletes_not_executed:
        # v3 records deletes and declines them. Saying so is the difference
        # between a disclosed skip and a silent one.
        print(
            f"\n{summary.deletes_not_executed} delete(s) are recorded and will not be "
            "applied: infrahub-sync v3 never deletes."
        )
    elif not summary.delete_operations_computed:
        print("\nDeletes were not computed for this plan, so none are reported.")


def cmd_plan(args: argparse.Namespace) -> int:
    """Work out what the sync would write, and save it for review."""
    from infrahub_sync.execution import execute_run

    run = prepare(Path(args.config), args.name, args.branch)
    plan = execute_run(
        run.instance,
        operation="plan",
        branch=args.branch,
        full_extract=True,
        print_diff=not args.quiet,
        _return_saved_plan=True,
    )
    _print_plan(plan)
    run_id = plan.manifest.run_id
    print(
        f"\nReview it with `forward-infrahub review {run_id} --config ... --name ...`, then "
        f"apply it with `--expected-checksum {plan.manifest.plan_checksum}`."
    )
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """Re-read a saved plan and prove it is the one that was planned."""
    from infrahub_sync.execution import execute_run

    run = prepare(Path(args.config), args.name, args.branch)
    plan = execute_run(run.instance, operation="verify", run_id=args.run_id)
    _print_plan(plan)
    if args.detail:
        for operation in plan.operations():
            print(f"  {operation.action:>6} {operation.kind:24} {operation.identity}")
    return 0 if plan.checksum_ok else 1


def cmd_apply(args: argparse.Namespace) -> int:
    """Write a reviewed plan to Infrahub."""
    import filelock
    from infrahub_sync.execution import execute_run
    from infrahub_sync.plan.ownership import ProvenWriteOwnership, WriteDispatchTracker

    run = prepare(Path(args.config), args.name, args.branch)
    applied: list[Any] = []
    lock = filelock.FileLock(Path(run.instance.directory) / ".forward-infrahub-apply.lock")
    with lock.acquire(timeout=args.lock_timeout):

        def prove() -> None:
            # The boundary v3 asks for: proof that this process still holds the
            # right to write, checked before every operation it dispatches.
            if not lock.is_locked:
                raise ForwardError("the apply lock was lost; nothing further was written")

        result = execute_run(
            run.instance,
            operation="apply",
            confirm_writes=True,
            branch=args.branch,
            run_id=args.run_id,
            expected_checksum=args.expected_checksum,
            ownership=ProvenWriteOwnership(prove=prove, tracker=WriteDispatchTracker()),
            record_applied=applied.append,
        )
    print(f"status: {result.status}")
    for action, count in sorted(result.summary.items()):
        print(f"{action:>9}: {count}")
    for record in applied:
        skipped = len(record.skipped_delete_operations)
        if skipped:
            print(f"\n{skipped} delete(s) were planned and not applied; v3 never deletes.")
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
    catalogue = query_catalogue()
    sources = [(name, catalogue.bundled_source(name)) for name in QUERY_FILENAMES]
    problems = check_contracts(sources)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"{len(sources)} bundled queries match their contracts")
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
    entries = audit(client, settings.query_directory, catalogue=query_catalogue())
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
    report = publish(
        client, settings.query_directory, catalogue=query_catalogue(), snapshot_id=snapshot_id
    )
    changed = getattr(report, "changed", None)
    print(
        f"published to {settings.query_directory}" if changed is not False else "already up to date"
    )
    entries = audit(client, settings.query_directory, catalogue=query_catalogue())
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

    def add_run_command(name: str, handler: Any, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        sub.set_defaults(handler=handler)
        sub.add_argument("--config", type=Path, required=True, help="the sync config.yml")
        sub.add_argument("--name", required=True, help="the sync's name in that config")
        sub.add_argument("--branch", default="main", help="the Infrahub branch")
        return sub

    add_run_command("models", cmd_models, "show the models built from the destination schema")
    plan_parser = add_run_command("plan", cmd_plan, "work out what a sync would write")
    plan_parser.add_argument("--quiet", action="store_true", help="do not print the diff")
    review_parser = add_run_command("review", cmd_review, "re-read a saved plan")
    review_parser.add_argument("run_id", help="the run a plan was saved under")
    review_parser.add_argument("--detail", action="store_true", help="list every operation")
    apply_parser = add_run_command("apply", cmd_apply, "write a reviewed plan to Infrahub")
    apply_parser.add_argument("run_id", help="the run whose plan to apply")
    apply_parser.add_argument(
        "--expected-checksum",
        required=True,
        help="the checksum `review` reported; the apply is refused if the plan has moved",
    )
    apply_parser.add_argument("--lock-timeout", type=float, default=60.0)

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

#!/usr/bin/env python3
"""Refuse to publish anything carrying a customer's identifiers.

This package is developed against real networks. Their device names, sites,
account numbers, network and snapshot ids, and credentials must never reach a
commit, a fixture, a document, or a built artifact — including the ones that
are only ever pushed to a private repository, because a repository's visibility
is a setting and this is not.

The deny-list lives outside the repository, so the list itself is not a leak.
Point ``FORWARD_INFRAHUB_SCRUB_LIST`` at it, or keep it at the default path.
One entry per line: a literal, or ``re:`` followed by a regular expression.
Lines beginning with ``#`` are comments. A missing list is an error, not an
all-clear: a check that silently passes when unconfigured is worse than none.

    python scripts/scrub_check.py [--staged]

Exit status is 0 when nothing matched, 1 when something did, and 2 when the
check could not run.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LIST = Path.home() / ".forward-infrahub-scrub.txt"
ENV_LIST = "FORWARD_INFRAHUB_SCRUB_LIST"

#: Directories git ignores but a tarball or a stray `cp -r` would carry.
UNTRACKED_ROOTS = (".infrahub-sync-cache", "dist")

#: Files whose bytes are not worth scanning as text.
BINARY_SUFFIXES = {".gz", ".parquet", ".whl", ".png", ".jpg", ".pdf", ".zip", ".pyc"}


@dataclass(frozen=True)
class Pattern:
    source: str
    regex: re.Pattern[str]

    @classmethod
    def parse(cls, line: str) -> Pattern | None:
        text = line.strip()
        if not text or text.startswith("#"):
            return None
        if text.startswith("re:"):
            return cls(text, re.compile(text[3:], re.IGNORECASE))
        return cls(text, re.compile(re.escape(text), re.IGNORECASE))


def load_patterns(path: Path) -> list[Pattern]:
    if not path.is_file():
        raise SystemExit(
            f"no scrub list at {path}. Write one (one literal or re: pattern per line) "
            f"or point {ENV_LIST} at it. This check does not pass by default."
        )
    return [
        p for line in path.read_text(encoding="utf-8").splitlines() if (p := Pattern.parse(line))
    ]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True, cwd=Path(__file__).parent.parent
    ).stdout


def tracked_files(staged: bool) -> Iterator[Path]:
    listing = git("diff", "--cached", "--name-only") if staged else git("ls-files")
    for name in listing.splitlines():
        path = Path(name)
        if path.is_file() and path.suffix not in BINARY_SUFFIXES:
            yield path


def untracked_files() -> Iterator[Path]:
    for root in UNTRACKED_ROOTS:
        base = Path(root)
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix not in BINARY_SUFFIXES:
                yield path


def scan_text(text: str, patterns: Iterable[Pattern], where: str) -> list[str]:
    hits = []
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern in patterns:
            if pattern.regex.search(line):
                # The matched token only, never the line: a report of a leak
                # must not repeat the leak.
                hits.append(f"{where}:{number}: {pattern.source}")
    return hits


def scan_paths(paths: Iterable[Path], patterns: list[Pattern]) -> list[str]:
    hits: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hits.extend(scan_text(text, patterns, str(path)))
        hits.extend(scan_text(str(path), patterns, f"{path} (path)"))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="only what is staged for commit")
    parser.add_argument("--list", type=Path, default=None, help="the deny-list to read")
    args = parser.parse_args(argv)

    path = args.list or Path(os.environ.get(ENV_LIST, DEFAULT_LIST))
    patterns = load_patterns(path)
    if not patterns:
        raise SystemExit(f"{path} declares no patterns; an empty list cannot clear anything")

    hits = scan_paths(tracked_files(args.staged), patterns)
    hits.extend(scan_paths(untracked_files(), patterns))
    if not args.staged:
        hits.extend(scan_text(git("log", "--all", "--format=%B"), patterns, "commit messages"))

    for hit in dict.fromkeys(hits):
        print(hit, file=sys.stderr)
    if hits:
        print(
            f"\n{len(set(hits))} place(s) carry something the scrub list names. "
            "Nothing here may be published until they are gone.",
            file=sys.stderr,
        )
        return 1
    print(f"clean against {len(patterns)} pattern(s) from {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

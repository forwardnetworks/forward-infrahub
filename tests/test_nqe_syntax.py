"""Mistakes Forward's NQE parser rejects and the offline fixture cannot see.

The replay fixture serves rows by query name, so it never parses query text. These
two forms parsed nowhere until a bundled query ran on a live Forward: a bare
``let x = foreach ...`` and a field read on a union such as ``VlanIdOrRange``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from forward_infrahub.queries import QUERY_FILENAMES, read_query_execution_source

BARE_LET_FOREACH = re.compile(r"^\s*let\s+\w+\s*=\s*foreach\b", re.MULTILINE)
UNION_FIELD_ACCESS = re.compile(r"\.(?:ID|RANGE)\b")


@pytest.mark.parametrize("filename", QUERY_FILENAMES)
def test_a_comprehension_bound_by_let_is_parenthesised(filename: str) -> None:
    source = read_query_execution_source(filename)
    assert not BARE_LET_FOREACH.search(source), (
        f"{filename}: write `let x = (foreach ...)`; the bare form is a parse error on Forward"
    )


@pytest.mark.parametrize("filename", QUERY_FILENAMES)
def test_a_union_is_read_with_when_and_not_by_field_access(filename: str) -> None:
    source = read_query_execution_source(filename)
    assert not UNION_FIELD_ACCESS.search(source), (
        f"{filename}: a VlanIdOrRange is read with `when x is ID(i) -> ...; RANGE(r) -> ...`"
    )


@pytest.mark.skipif(shutil.which("fwdctl") is None, reason="fwdctl is not installed")
@pytest.mark.parametrize("filename", QUERY_FILENAMES)
def test_the_offline_nqe_linter_accepts_every_bundled_query(
    filename: str, tmp_path: pytest.TempPathFactory
) -> None:
    path = tmp_path / filename
    path.write_text(read_query_execution_source(filename), encoding="utf-8")

    result = subprocess.run(
        [shutil.which("fwdctl") or "fwdctl", "nqe", "lint", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )

    findings = json.loads(result.stdout or "{}")
    errors = [
        f"line {d.get('line')}: {d.get('message')}"
        for d in (findings.get("diagnostics") or findings.get("findings") or [])
        if d.get("severity") == "error"
    ]
    assert result.returncode == 0 and not errors, f"{filename}: {errors}"

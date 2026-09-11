"""The gate that keeps a customer's identifiers out of anything published.

The fixture is the likeliest place for one to arrive, because it is written by
looking at a real network. These assertions describe what a synthetic fixture
looks like, so a row copied from a live run fails here rather than in a
repository somebody later makes public.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from scripts.scrub_check import Pattern, load_patterns, scan_text

from .conftest import FIXTURE

DEVICE_NAME = re.compile(r"^device-\d{3}$")
SITE_NAME = re.compile(r"^[0-9]+ [A-Z] [A-Z]+ (ST|STREET)$", re.IGNORECASE)
#: The ranges a fixture may use: RFC 5737 and RFC 3849 documentation blocks,
#: and RFC 1918 private space. None of them names a host anyone can reach.
DOCUMENTATION_ADDRESS = re.compile(
    r"^(192\.0\.2\.|198\.51\.100\.|203\.0\.113\.|2001:db8:|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|127\.)"
)
TOKEN_LIKE = re.compile(r"[A-Za-z0-9_-]{32,}")

PAYLOAD = json.loads(FIXTURE.read_text())


def rows(table: str) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = PAYLOAD.get(table, [])
    return tables


def test_every_device_is_named_synthetically() -> None:
    names = {row["name"] for row in rows("forward_devices")}
    names |= {row["device"] for row in rows("forward_interfaces")}
    for name in names:
        assert DEVICE_NAME.match(name), f"{name!r} does not look synthetic"


def test_every_site_is_a_made_up_address() -> None:
    for row in rows("forward_locations"):
        assert SITE_NAME.match(row["name"]), f"{row['name']!r} does not look synthetic"


def test_addresses_come_from_the_documentation_ranges() -> None:
    """A real address in a fixture is a real subnet somebody can look up."""
    for row in rows("forward_ip_addresses"):
        assert DOCUMENTATION_ADDRESS.match(row["host_ip"]), row["host_ip"]


def test_the_cloud_account_is_the_documented_example_id() -> None:
    for row in rows("forward_cloud_accounts"):
        assert row["account_id"] == "111122223333", row["account_id"]


def test_nothing_in_the_fixture_looks_like_a_token() -> None:
    hits = TOKEN_LIKE.findall(FIXTURE.read_text())
    assert not hits, f"token-shaped strings in the fixture: {hits[:3]}"


def test_a_pattern_matches_a_literal_and_a_regex() -> None:
    literal = Pattern.parse("Acme Networks")
    assert literal is not None
    assert literal.regex.search("site: acme networks")
    expression = Pattern.parse("re:net-[0-9]{4}")
    assert expression is not None
    assert expression.regex.search("network_id: net-1234")
    assert Pattern.parse("  # a comment") is None


def test_a_report_names_the_pattern_and_never_the_line() -> None:
    """A report of a leak must not repeat the leak."""
    pattern = Pattern.parse("acme-core-01")
    assert pattern is not None
    hits = scan_text("the device is acme-core-01\n", [pattern], "f.txt")
    assert hits == ["f.txt:1: acme-core-01"]
    assert "the device is" not in hits[0]


def test_a_missing_list_is_an_error_not_an_all_clear(tmp_path: Path) -> None:
    """A check that passes when unconfigured is worse than no check."""
    with pytest.raises(SystemExit, match="does not pass by default"):
        load_patterns(tmp_path / "absent.txt")

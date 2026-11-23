"""Errors raised while a query is run or its reference is built keep naming what failed."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from forward_infrahub.exceptions import ForwardLoadError, InvalidConfiguration
from forward_infrahub.fetch import QueryRunner, resolve_queries
from forward_infrahub.settings import AdapterSettings

SETTINGS = AdapterSettings(url="https://fwd.example", network_id="net-1")


def test_a_query_that_cannot_be_sharded_names_itself() -> None:
    """A source with no device loop is refused with the query's name, as it was before."""
    runner = QueryRunner(SimpleNamespace(), SETTINGS, "snap-1")
    with pytest.raises(ForwardLoadError, match=r"query 'forward_x' failed: .*device comprehension"):
        runner._fetch_shard("select 1", ["a"], "net-1", "forward_x")


@pytest.mark.parametrize("commit", ["abc1234", "ab"])
def test_an_abbreviated_commit_in_a_mapping_is_refused_as_invalid_configuration(
    commit: str,
) -> None:
    """The SDK checks a commit only when it is read; the reference is checked when it is built."""
    with pytest.raises(InvalidConfiguration, match="commit"):
        resolve_queries(SimpleNamespace(), [f"nqe-id:FQ_x@{commit}"], SETTINGS, SimpleNamespace())


def test_a_full_commit_and_head_are_not_refused() -> None:
    """A full hash is pinned and `head` is accepted (treated as unpinned); neither is an error."""
    forward = SimpleNamespace(resolve_query=lambda ref: ref)
    full = "a" * 40
    resolved = resolve_queries(
        forward, [f"nqe-id:FQ_x@{full}", "nqe-id:FQ_y@head"], SETTINGS, SimpleNamespace()
    )
    assert set(resolved) == {f"nqe-id:FQ_x@{full}", "nqe-id:FQ_y@head"}


def test_a_resolved_reference_with_an_abbreviated_commit_is_refused_as_invalid_configuration() -> (
    None
):
    """Forward returning a short hash for a library query is a configuration error, not a crash."""
    forward = SimpleNamespace(resolve_query=lambda ref: ref.resolved("FQ_x", "abc1234"))
    with pytest.raises(InvalidConfiguration, match="commit"):
        resolve_queries(forward, ["nqe-path:/org/forward_x"], SETTINGS, SimpleNamespace())

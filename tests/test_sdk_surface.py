"""What the adapter assumes about the SDK, checked against the real SDK.

Every fetch in this package funnels failures into ``ForwardLoadError`` with a
single ``except ForwardError``. That is only a funnel if the SDK actually
raises inside that hierarchy. Until forward-sdk 0.1.5 it did not: a response
body it could not parse surfaced pydantic's ``ValidationError``, which is not a
``ForwardError``, so it escaped the handler and ended the run with a parser
traceback naming SDK internals rather than the query that failed.

These tests drive the installed SDK through a mock transport instead of a stub
of it. A stub would encode the same belief as the code under test and agree
with it whether or not the belief is true.
"""

from __future__ import annotations

import httpx
import pytest
from forward_sdk import ForwardClient, ForwardError

#: A well-formed HTTP response whose body is nothing the SDK models.
UNPARSEABLE_BODY = {"totally": "unexpected", "shape": [1, 2, 3]}


@pytest.fixture
def client() -> ForwardClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=UNPARSEABLE_BODY)

    return ForwardClient(
        "https://forward.invalid",
        username="user",
        password="password",
        retries=0,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize("call", ["networks", "devices"])
def test_an_unparseable_body_raises_a_forward_error(client: ForwardClient, call: str) -> None:
    """The error the fetch path catches, not one that walks past it."""
    service = getattr(client, call)
    kwargs = {} if call == "networks" else {"network_id": "1"}
    with pytest.raises(ForwardError):
        service.list(**kwargs)


def test_every_sdk_error_the_adapter_names_descends_from_forward_error() -> None:
    """`except ForwardError` has to be the whole net, not most of it.

    A future SDK release adding an exception outside the hierarchy would
    reopen the hole 0.1.5 closed, silently, on a version bump.
    """
    import forward_sdk

    outside = sorted(
        name
        for name in dir(forward_sdk)
        if name.endswith("Error")
        and isinstance(getattr(forward_sdk, name), type)
        and issubclass(getattr(forward_sdk, name), BaseException)
        and not issubclass(getattr(forward_sdk, name), ForwardError)
    )
    assert outside == [], f"SDK exceptions outside ForwardError: {outside}"


def _runner(nqe: object, **settings: object) -> object:
    from forward_infrahub.fetch import QueryRunner
    from forward_infrahub.forward_client import ForwardSdkAdapter
    from forward_infrahub.settings import AdapterSettings

    class _Client:
        pass

    fake = _Client()
    fake.nqe = nqe  # type: ignore[attr-defined]
    return QueryRunner(
        ForwardSdkAdapter(fake), AdapterSettings(url="https://fwd.example", **settings), "snap-1"
    )


def test_fetch_passes_only_keywords_the_real_nqe_query_accepts(client: ForwardClient) -> None:
    """A stub accepts any keyword; the real signature is the check."""
    import inspect

    from forward_sdk.nqe import QueryRef

    real = inspect.signature(client.nqe.query)
    seen: dict[str, object] = {}

    class _Nqe:
        def query(self, ref: object, **kwargs: object) -> list[dict[str, object]]:
            seen.update(kwargs)
            return [{"name": "r1"}]

    runner = _runner(_Nqe(), outcome_retries=3, timeout=77.0, page_size=500)
    rows = runner._fetch(QueryRef.inline("select 1"), "net-1", label="q")  # type: ignore[attr-defined]

    real.bind(QueryRef.inline("select 1"), **seen)
    assert rows == [{"name": "r1"}]
    assert seen["outcome_retries"] == 3
    assert seen["timeout"] == 77.0
    assert seen["page_size"] == 500
    assert seen["snapshot_id"] == "snap-1"
    assert seen["network_id"] == "net-1"
    assert seen["guards"] is not None


def test_a_query_that_ends_in_an_execution_error_fails_the_load(client: ForwardClient) -> None:
    from forward_sdk import ForwardExecutionError

    from forward_infrahub.exceptions import ForwardLoadError

    class _Nqe:
        def query(self, ref: object, **kwargs: object) -> list[dict[str, object]]:
            raise ForwardExecutionError("failed", outcome="SYSTEM_ERROR", attempts=3)

    runner = _runner(_Nqe())
    with pytest.raises(ForwardLoadError, match="failed"):
        # A source with the device loop every shardable query opens with, so it can be
        # sharded and the query actually runs (a source without it is refused first).
        source = "foreach device in network.devices\nselect 1"
        runner._fetch_shard(source, ["a"], "net-1", "q")  # type: ignore[attr-defined]

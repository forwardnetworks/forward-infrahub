"""The queries and schema must survive being packaged, not just work from a checkout."""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from forward_infrahub.queries import QUERY_FILENAMES, read_query_source

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_queries_load_through_importlib_resources() -> None:
    """Read as package data, so they work from a wheel and not only from source."""
    for filename in QUERY_FILENAMES:
        assert read_query_source(filename).strip()


def test_the_schema_extension_ships_with_the_package() -> None:
    from importlib import resources

    schema = resources.files("forward_infrahub.schemas") / "forward_cloud.yml"
    assert "CloudAccount" in schema.read_text(encoding="utf-8") or "Account" in schema.read_text(
        encoding="utf-8"
    )


def test_the_example_schema_matches_the_packaged_one() -> None:
    """Operators load the copy in examples/; it must not drift from the source."""
    from importlib import resources

    packaged = (resources.files("forward_infrahub.schemas") / "forward_cloud.yml").read_text()
    example = (REPO_ROOT / "examples/forwardnetworks_to_infrahub/schema.yml").read_text()
    assert packaged == example


@pytest.mark.slow
def test_the_wheel_contains_the_queries_and_schema(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH")
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    wheel = next(tmp_path.glob("*.whl"))
    names = zipfile.ZipFile(wheel).namelist()
    assert sum(1 for name in names if name.endswith(".nqe")) == len(QUERY_FILENAMES)
    assert any(name.endswith("schemas/forward_cloud.yml") for name in names)
    assert any(name.endswith("entry_points.txt") for name in names)

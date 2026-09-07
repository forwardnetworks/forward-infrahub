"""infrahub-sync must be able to find the adapter without any config help."""

from __future__ import annotations

from diffsync import Adapter, DiffSyncModel
from infrahub_sync.plugin_loader import PluginLoader

from forward_infrahub.adapter import ForwardnetworksAdapter, ForwardnetworksModel


def test_the_entry_point_resolves_the_adapter() -> None:
    """`source.name: forwardnetworks` alone is enough; no `adapter:` line."""
    assert PluginLoader().resolve("forwardnetworks") is ForwardnetworksAdapter


def test_the_entry_point_resolves_the_model() -> None:
    """A separate loader, as the generated sync_models.py uses."""
    loader = PluginLoader.from_env_and_args()
    resolved = loader.resolve("forwardnetworks", default_class_candidates=("Model",))
    assert resolved is ForwardnetworksModel


def test_the_classes_are_what_the_loader_looks_for() -> None:
    """The loader picks the first class deriving from the base it wants."""
    assert issubclass(ForwardnetworksAdapter, Adapter)
    assert issubclass(ForwardnetworksModel, DiffSyncModel)
    assert not issubclass(ForwardnetworksAdapter, DiffSyncModel)


def test_the_generated_adapter_class_name_would_match() -> None:
    """`infrahub-sync generate` writes `<Camel>Sync`, and imports by that name."""
    assert PluginLoader().camelize("forwardnetworks") == "Forwardnetworks"

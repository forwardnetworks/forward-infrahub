"""Checks on the installed infrahub-sync, which has two bugs this adapter hits.

Neither is in this package and neither can be worked around from here, but both
stop a sync dead the first time a mapping touches an attribute that is not
text, so it is better to say so by name than to let the traceback arrive
unexplained. The patches are in ``upstream/`` in this repository.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EnvironmentProblem:
    name: str
    detail: str
    patch: str

    def __str__(self) -> str:
        return f"{self.name}\n  {self.detail}\n  Patch: upstream/{self.patch}"


def generator_types_every_field_as_text() -> EnvironmentProblem | None:
    """infrahub-sync's generator decides a field's type with an isinstance check.

    ``client.schema.all()`` returns the ``*SchemaAPI`` classes, which are
    siblings of the ``*Schema`` ones rather than subclasses, so the check never
    matches and every field falls through to ``str``. A Number then rejects its
    own value, and a cardinality-many relationship is typed as a single string.
    """
    try:
        from infrahub_sdk.schema import AttributeSchemaAPI
        from infrahub_sync.generator import get_kind
    except ImportError:  # pragma: no cover - infrahub-sync is a hard dependency
        return None

    # Ask it, rather than reading it: a fix may name the class anywhere, and an
    # earlier version of this check looked inside the function while the patch
    # changed a constant beside it.
    probe: Any = AttributeSchemaAPI.model_construct(name="probe", kind="Number", optional=False)
    if get_kind(probe).startswith("int"):
        return None
    return EnvironmentProblem(
        "infrahub-sync types every generated field as str",
        "Its generator isinstance-checks AttributeSchema while the SDK returns "
        "AttributeSchemaAPI, so a Number, Boolean or List attribute is typed str and "
        "rejects its own value. Any adapter with such an attribute fails to load.",
        "0001-generator-accept-schema-api-classes.patch",
    )


def destination_stringifies_every_attribute() -> EnvironmentProblem | None:
    """The Infrahub adapter converts every non-string value it reads back.

    Consistent with models whose fields are all ``str``, and wrong once they are
    not: a List attribute comes back as the printed form of a list, so a second
    run cannot load what the first one wrote.
    """
    try:
        from infrahub_sync.adapters.infrahub import InfrahubAdapter
    except ImportError:  # pragma: no cover
        return None

    source = inspect.getsource(InfrahubAdapter.infrahub_node_to_diffsync)
    if "not isinstance(val, str)" not in source:
        return None
    return EnvironmentProblem(
        "infrahub-sync stringifies every attribute it reads back",
        "Its Infrahub adapter converts any non-string attribute to str(), so a List "
        "returns as \"['10.0.0.0/24']\" and the next run fails to load the destination.",
        "0002-destination-preserve-attribute-types.patch",
    )


def check_environment() -> list[EnvironmentProblem]:
    """Every known problem with the installed infrahub-sync."""
    found = (generator_types_every_field_as_text(), destination_stringifies_every_attribute())
    return [problem for problem in found if problem is not None]

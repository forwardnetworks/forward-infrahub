"""Comparing how this sync identifies an object with how Infrahub does.

A DiffSync model's identifiers decide which source rows are the same object.
Infrahub resolves an upsert against a node's human-friendly id. When those
disagree, the sync does not fail: rows that Infrahub considers one node are
written one after another, each overwriting the last, and the run reports every
write as a success.

That is how a mapping came to load 127 VLANs and store 57. IpamVLAN inherits a
human-friendly id of name alone, so VLANs sharing a name merged however they
were scoped. Nothing in either system reports it, because neither is wrong on
its own terms; only comparing them shows it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "check_identities",
]


@dataclass(frozen=True)
class IdentityCheck:
    kind: str
    identifiers: tuple[str, ...]
    human_friendly_id: tuple[str, ...]
    source: str
    #: Identifiers absent from the human-friendly id whose values nonetheless
    #: reach it, because a field in the id is built from them.
    carried_by_transform: tuple[str, ...] = ()

    @property
    def unrepresented(self) -> tuple[str, ...]:
        """Identifiers that cannot influence what Infrahub considers this node."""
        if not self.human_friendly_id:
            return ()
        named = {part.split("__", 1)[0] for part in self.human_friendly_id}
        return tuple(sorted(set(self.identifiers) - named - set(self.carried_by_transform)))

    @property
    def merges(self) -> bool:
        """Whether Infrahub would treat distinct source rows as one node.

        No human-friendly id means upserts fall back to uniqueness constraints,
        which this mapping is written against, so that case is not a risk.
        """
        return bool(self.unrepresented)

    @property
    def summary(self) -> str:
        if not self.human_friendly_id:
            return f"{self.kind}: no human-friendly id, uniqueness constraints apply"
        if self.merges:
            return (
                f"{self.kind}: identified here by {list(self.identifiers)}, but Infrahub's "
                f"human-friendly id is {list(self.human_friendly_id)} ({self.source}). "
                f"Rows differing only by {list(self.unrepresented)} would be written over "
                "each other, silently and one at a time. Build one of the id's fields from "
                "them, or map a different kind."
            )
        if self.carried_by_transform:
            return (
                f"{self.kind}: ok, {list(self.carried_by_transform)} reaches Infrahub's "
                "id through a transform"
            )
        return f"{self.kind}: ok"


def _hfid_for(
    kind: str, nodes: Mapping[str, Any], generics: Mapping[str, Any]
) -> tuple[list[str], str]:
    node = nodes.get(kind) or generics.get(kind) or {}
    own = node.get("human_friendly_id")
    if own:
        return list(own), "its own"
    for parent in node.get("inherit_from") or []:
        inherited = (generics.get(parent) or {}).get("human_friendly_id")
        if inherited:
            return list(inherited), f"inherited from {parent}"
    return [], ""


def check_identities(
    schema: Mapping[str, Any], schema_mapping: Iterable[Mapping[str, Any]]
) -> list[IdentityCheck]:
    """Compare each mapped kind's identifiers with Infrahub's own identity."""
    nodes = {n["kind"]: n for n in schema.get("nodes") or []}
    generics = {g["kind"]: g for g in schema.get("generics") or []}
    checks: list[IdentityCheck] = []
    for element in schema_mapping:
        kind = element["name"]
        identifiers = tuple(element.get("identifiers") or ())
        hfid, source = _hfid_for(kind, nodes, generics)
        carried = _carried_by_transform(element, hfid, identifiers)
        checks.append(IdentityCheck(kind, identifiers, tuple(hfid), source, carried))
    return checks


def _carried_by_transform(
    element: Mapping[str, Any], hfid: Iterable[str], identifiers: Iterable[str]
) -> tuple[str, ...]:
    """Identifiers whose values reach the human-friendly id through a transform.

    A field named in the id may be built from the identifying columns rather
    than read straight from a row, which is how a VLAN name can carry the id
    and the site it is scoped to. Then the id does distinguish the rows, even
    though it does not name the identifiers.
    """
    fields = {f["name"]: f.get("mapping") for f in element.get("fields") or []}
    sources = {f["name"]: f.get("mapping") for f in element.get("fields") or []}
    expressions = {t["field"]: t.get("expression", "") for t in element.get("transforms") or []}
    in_id = {part.split("__", 1)[0] for part in hfid}
    text = " ".join(expressions.get(mapped, "") for name, mapped in fields.items() if name in in_id)
    if not text:
        return ()
    carried = []
    for identifier in identifiers:
        column = sources.get(identifier) or identifier
        if f"{{{{ {column} }}}}" in text or f"{{{{{column}}}}}" in text:
            carried.append(identifier)
    return tuple(sorted(carried))

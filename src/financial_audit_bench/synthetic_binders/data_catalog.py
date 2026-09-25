"""Small manifest-backed loader for synthetic-binder runtime data."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable
from types import MappingProxyType
from typing import Any

type ImmutableJson = (
    None
    | bool
    | int
    | float
    | str
    | tuple["ImmutableJson", ...]
    | Mapping[str, "ImmutableJson"]
)

_PACKAGE = "financial_audit_bench.synthetic_binders"
_FORBIDDEN_AUTHORED_KEYS = {
    "donor_id",
    "epsilon",
    "noise",
    "rho",
    "sampled_value",
    "sensitivity",
    "support_count",
}


class ResourceKind(StrEnum):
    AUTHORED_POLICY = "authored_policy"
    REGISTRY = "registry"


class CatalogError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    resource_id: str
    kind: ResourceKind
    path: str


@dataclass(frozen=True, slots=True)
class CatalogResource:
    resource_id: str
    references: tuple[str, ...]
    values: Mapping[str, ImmutableJson]


def to_mutable_json(value: ImmutableJson) -> Any:
    if isinstance(value, Mapping):
        return {key: to_mutable_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [to_mutable_json(item) for item in value]
    return value


def _freeze(value: Any) -> ImmutableJson:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


class _Catalog:
    def __init__(self, root: Traversable):
        self.root = root
        self.entries: dict[str, ManifestEntry] = {}
        self.cache: dict[str, CatalogResource] = {}
        self.manifest = self._load_manifest()

    def _resolve(self, path: str) -> Traversable:
        parts = path.split("/")
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise CatalogError(f"unsafe catalog path: {path!r}")
        node = self.root
        for part in parts:
            node = node.joinpath(part)
        return node

    def _read(self, path: str) -> dict[str, Any]:
        node = self._resolve(path)
        if not node.is_file():
            raise CatalogError(f"catalog file does not exist: {path}")
        try:
            value = json.loads(
                node.read_text("utf-8"), object_pairs_hook=_unique_object
            )
        except json.JSONDecodeError as exc:
            raise CatalogError(f"invalid catalog JSON {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise CatalogError(f"{path}: catalog JSON must be an object")
        return value

    def _load_manifest(self) -> tuple[ManifestEntry, ...]:
        payload = self._read("manifest.json")
        rows = payload.get("resources")
        if not isinstance(rows, list):
            raise CatalogError("manifest resources must be an array")
        entries: list[ManifestEntry] = []
        paths: set[str] = set()
        for row in rows:
            try:
                entry = ManifestEntry(
                    resource_id=str(row["resource_id"]),
                    kind=ResourceKind(row["kind"]),
                    path=str(row["path"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise CatalogError(f"invalid manifest entry: {row!r}") from exc
            if entry.resource_id in self.entries or entry.path in paths:
                raise CatalogError(f"duplicate manifest entry: {entry.resource_id}")
            if not self._resolve(entry.path).is_file():
                raise CatalogError(f"missing catalog resource: {entry.path}")
            self.entries[entry.resource_id] = entry
            paths.add(entry.path)
            entries.append(entry)
        return tuple(entries)

    def load(self, resource_id: str, kind: ResourceKind) -> CatalogResource:
        entry = self.entries.get(resource_id)
        if entry is None:
            raise CatalogError(f"unknown catalog resource: {resource_id!r}")
        if entry.kind != kind:
            raise CatalogError(f"{resource_id}: expected {kind}, found {entry.kind}")
        if resource_id in self.cache:
            return self.cache[resource_id]
        payload = self._read(entry.path)
        if payload.get("resource_id") != entry.resource_id:
            raise CatalogError(f"{entry.path}: resource does not match manifest")
        if kind is ResourceKind.AUTHORED_POLICY:
            self._reject_empirical(payload, entry.path)
        try:
            references = tuple(payload.get("references", ()))
            values = payload["values"]
        except (KeyError, TypeError) as exc:
            raise CatalogError(f"{entry.path}: incomplete resource envelope") from exc
        missing = set(references) - set(self.entries)
        if missing:
            raise CatalogError(f"{entry.path}: unknown references: {sorted(missing)}")
        resource = CatalogResource(resource_id, references, _freeze(values))
        self.cache[resource_id] = resource
        return resource

    def _reject_empirical(self, value: Any, source: str) -> None:
        if isinstance(value, dict):
            forbidden = _FORBIDDEN_AUTHORED_KEYS & set(value)
            if forbidden:
                raise CatalogError(
                    f"{source}: forbidden authored keys {sorted(forbidden)}"
                )
            for nested in value.values():
                self._reject_empirical(nested, source)
        elif isinstance(value, list):
            for nested in value:
                self._reject_empirical(nested, source)


@lru_cache(maxsize=1)
def _catalog() -> _Catalog:
    return _Catalog(resources.files(_PACKAGE).joinpath("data"))


def load_authored_policy(resource_id: str) -> CatalogResource:
    return _catalog().load(resource_id, ResourceKind.AUTHORED_POLICY)


def load_registry(resource_id: str) -> CatalogResource:
    return _catalog().load(resource_id, ResourceKind.REGISTRY)

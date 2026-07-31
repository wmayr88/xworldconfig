"""Builds per-folder, per-type instance counts by decompiling every tile in a
scenery pack folder, for display in the object-type tree."""
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TypeCount:
    type_name: str
    kind: str  # "OBJECT" | "POLYGON" | "NETWORK"
    count: int


def scan_folder(scenery_pack_dir: Path) -> list[TypeCount]:
    raise NotImplementedError

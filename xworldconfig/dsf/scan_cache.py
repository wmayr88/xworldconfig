"""Persists per-tile object/polygon/network instance counts so repeat scans
skip decompiling tiles that haven't changed (matched by size + mtime), which
matters a lot given how large some simHeaven tiles are - a single footprints
tile can take a couple of seconds to decompile and parse on its own.

Lives next to the executable alongside the settings file (see
xworldconfig.paths.scan_cache_path). It's purely a speed optimization, safe
to delete at any time - a missing or corrupt cache just means the next scan
falls back to decompiling everything."""
import json
from dataclasses import dataclass
from pathlib import Path

from xworldconfig.paths import scan_cache_path


@dataclass
class _CacheEntry:
    size: int
    mtime: float
    counts: dict[str, dict[str, int]]


class ScanCache:
    def __init__(self):
        self._entries: dict[str, _CacheEntry] = {}
        self._load()

    def _load(self) -> None:
        path = scan_cache_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for tile_path, raw in data.items():
            self._entries[tile_path] = _CacheEntry(raw["size"], raw["mtime"], raw["counts"])

    def get(self, tile: Path, size: int, mtime: float) -> dict[str, dict[str, int]] | None:
        entry = self._entries.get(str(tile))
        if entry and entry.size == size and entry.mtime == mtime:
            return entry.counts
        return None

    def put(self, tile: Path, size: int, mtime: float, counts: dict[str, dict[str, int]]) -> None:
        self._entries[str(tile)] = _CacheEntry(size, mtime, counts)

    def prune_missing(self, folder: Path, existing_tiles: set[str]) -> None:
        prefix = str(folder)
        self._entries = {
            path: entry
            for path, entry in self._entries.items()
            if not path.startswith(prefix) or path in existing_tiles
        }

    def save(self) -> None:
        data = {
            path: {"size": e.size, "mtime": e.mtime, "counts": e.counts}
            for path, e in self._entries.items()
        }
        scan_cache_path().write_text(json.dumps(data), encoding="utf-8")

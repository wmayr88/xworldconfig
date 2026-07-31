"""Builds per-folder, per-type instance counts by decompiling every tile in a
scenery pack folder, for display in the object-type tree.

This intentionally does not go through xworldconfig.dsf.text_model - that
model retains every instance's exact source lines (needed later by apply.py
to reconstruct/filter a tile), which is far more memory than a folder-wide
count needs. A single simHeaven footprints tile can decompile to several
million lines, so this does a single streaming pass per tile and only keeps
running counts, never the instance lines themselves.

Unchanged tiles (by size + mtime) are served from xworldconfig.dsf.scan_cache
instead of being re-decompiled, and tiles that do need scanning are processed
concurrently with a thread pool - decompile is a subprocess call, so it
releases the GIL while DSFTool runs, letting multiple tiles' decompiles
overlap on multi-core machines."""
import concurrent.futures
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from xworldconfig.dsf.dsftool import DSFToolError, decompile
from xworldconfig.dsf.scan_cache import ScanCache

_KINDS = ("OBJECT", "POLYGON", "NETWORK")


@dataclass
class TypeCount:
    type_name: str
    kind: str  # "OBJECT" | "POLYGON" | "NETWORK"
    count: int


@dataclass
class ScanResult:
    counts: list[TypeCount]
    failed_tiles: list[Path]


def scan_folder(scenery_pack_dir: Path, max_workers: int | None = None) -> ScanResult:
    nav_data_dir = scenery_pack_dir / "Earth nav data"
    tiles = sorted(nav_data_dir.glob("**/*.dsf")) if nav_data_dir.is_dir() else []

    cache = ScanCache()
    totals: dict[str, dict[str, int]] = {kind: {} for kind in _KINDS}
    failed_tiles: list[Path] = []
    to_scan: list[Path] = []
    stats: dict[Path, tuple[int, float]] = {}

    for tile in tiles:
        st = tile.stat()
        stats[tile] = (st.st_size, st.st_mtime)
        cached = cache.get(tile, st.st_size, st.st_mtime)
        if cached is not None:
            _merge(totals, cached)
        else:
            to_scan.append(tile)

    if to_scan:
        workers = max_workers or (os.cpu_count() or 4)
        with tempfile.TemporaryDirectory(prefix="xworldconfig-scan-") as tmp_dir:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_scan_tile, tile, Path(tmp_dir)): tile for tile in to_scan}
                for future in concurrent.futures.as_completed(futures):
                    tile = futures[future]
                    try:
                        counts = future.result()
                    except DSFToolError:
                        failed_tiles.append(tile)
                        continue
                    size, mtime = stats[tile]
                    cache.put(tile, size, mtime, counts)
                    _merge(totals, counts)

    cache.prune_missing(scenery_pack_dir, {str(t) for t in tiles})
    cache.save()

    counts = [
        TypeCount(type_name, kind, count)
        for kind in _KINDS
        for type_name, count in totals[kind].items()
    ]
    counts.sort(key=lambda c: (c.kind, -c.count, c.type_name))
    return ScanResult(counts, failed_tiles)


def _scan_tile(tile: Path, tmp_dir: Path) -> dict[str, dict[str, int]]:
    text_path = tmp_dir / f"{uuid.uuid4().hex}.txt"
    try:
        decompile(tile, text_path)
        return _count_tile(text_path)
    finally:
        text_path.unlink(missing_ok=True)


def _merge(totals: dict[str, dict[str, int]], counts: dict[str, dict[str, int]]) -> None:
    for kind, by_name in counts.items():
        target = totals[kind]
        for name, count in by_name.items():
            target[name] = target.get(name, 0) + count


def _count_tile(text_path: Path) -> dict[str, dict[str, int]]:
    def_names: dict[str, list[str]] = {kind: [] for kind in _KINDS}
    index_counts: dict[str, dict[int, int]] = {kind: {} for kind in _KINDS}

    with text_path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("OBJECT_DEF "):
                def_names["OBJECT"].append(line[len("OBJECT_DEF "):].strip())
            elif line.startswith("POLYGON_DEF "):
                def_names["POLYGON"].append(line[len("POLYGON_DEF "):].strip())
            elif line.startswith("NETWORK_DEF "):
                def_names["NETWORK"].append(line[len("NETWORK_DEF "):].strip())
            elif line.startswith("OBJECT "):
                _bump(index_counts["OBJECT"], line)
            elif line.startswith("BEGIN_POLYGON "):
                _bump(index_counts["POLYGON"], line)
            elif line.startswith("BEGIN_SEGMENT "):
                _bump(index_counts["NETWORK"], line)

    result: dict[str, dict[str, int]] = {kind: {} for kind in _KINDS}
    for kind in _KINDS:
        names = def_names[kind]
        for index, count in index_counts[kind].items():
            name = names[index] if index < len(names) else f"<unknown index {index}>"
            result[kind][name] = result[kind].get(name, 0) + count
    return result


def _bump(index_counts: dict[int, int], line: str) -> None:
    index = int(line.split(" ", 2)[1])
    index_counts[index] = index_counts.get(index, 0) + 1

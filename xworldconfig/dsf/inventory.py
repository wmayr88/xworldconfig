"""Builds per-folder, per-type instance counts by decompiling every tile in
one or more scenery pack folders, for display in the object-type tree.

This intentionally does not go through xworldconfig.dsf.text_model - that
model retains every instance's exact source lines (needed later by apply.py
to reconstruct/filter a tile), which is far more memory than a folder-wide
count needs. A single simHeaven footprints tile can decompile to several
million lines, so this does a single streaming pass per tile and only keeps
running counts, never the instance lines themselves.

Each type's count is split into active (currently present in the live tile,
from decompiling it) and disabled (recorded in the tile's .xwcdisabled
sidecar - see xworldconfig.dsf.backup - which apply.py maintains). Reading a
sidecar is cheap (a small gzip-compressed text file, no DSFTool subprocess
needed) compared to decompiling the live tile, so this only adds real cost
for the minority of tiles that have actually been touched.

Unchanged files (by size + mtime) are served from xworldconfig.dsf.scan_cache
instead of being re-decompiled. scan_folders() flattens every tile across ALL
requested folders into one shared thread pool and one shared progress count,
rather than a separate pool per folder - this avoids the tail-end
underutilization of re-spinning a pool per folder when scanning many folders
at once, and gives a single, meaningful (completed, total) progress signal
for a "scan everything" operation. scan_folder() is a thin single-folder
wrapper around it, used for the lazy per-category scan triggered by
expanding a tree item."""
import concurrent.futures
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from xworldconfig.dsf.backup import has_disabled_records, load_disabled_records
from xworldconfig.dsf.concurrency import default_worker_count
from xworldconfig.dsf.dsftool import DSFToolError, decompile
from xworldconfig.dsf.scan_cache import ScanCache
from xworldconfig.dsf.text_model import kind_of_line

_KINDS = ("OBJECT", "POLYGON", "NETWORK")
_EMPTY_COUNTS: dict[str, dict[str, int]] = {kind: {} for kind in _KINDS}


@dataclass
class TypeCount:
    type_name: str
    kind: str  # "OBJECT" | "POLYGON" | "NETWORK"
    active_count: int
    disabled_count: int


@dataclass
class ScanResult:
    counts: list[TypeCount]
    failed_tiles: list[Path]


@dataclass
class TileCount:
    tile: Path
    active_count: int
    disabled_count: int


def scan_folder(
    scenery_pack_dir: Path,
    max_workers: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> ScanResult:
    return scan_folders([scenery_pack_dir], max_workers=max_workers, on_progress=on_progress)[scenery_pack_dir]


def scan_folders(
    scenery_pack_dirs: list[Path],
    max_workers: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[Path, ScanResult]:
    tiles_by_folder = {d: _list_tiles(d) for d in scenery_pack_dirs}

    all_tiles: set[Path] = set()
    for tiles in tiles_by_folder.values():
        all_tiles.update(tiles)

    cache = ScanCache()
    stats: dict[Path, tuple[int, float]] = {}
    counts_by_path: dict[Path, dict[str, dict[str, int]]] = {}
    to_scan: list[Path] = []
    failed: set[Path] = set()

    for tile in all_tiles:
        st = tile.stat()
        stats[tile] = (st.st_size, st.st_mtime)
        cached = cache.get(tile, st.st_size, st.st_mtime)
        if cached is not None:
            counts_by_path[tile] = cached
        else:
            to_scan.append(tile)

    total = len(all_tiles)
    completed = total - len(to_scan)
    if on_progress:
        on_progress(completed, total)

    if to_scan:
        workers = max_workers or default_worker_count()
        with tempfile.TemporaryDirectory(prefix="xworldconfig-scan-") as tmp_dir:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_scan_tile, tile, Path(tmp_dir)): tile for tile in to_scan}
                for future in concurrent.futures.as_completed(futures):
                    tile = futures[future]
                    try:
                        counts = future.result()
                    except DSFToolError:
                        failed.add(tile)
                        counts_by_path[tile] = _EMPTY_COUNTS
                    else:
                        size, mtime = stats[tile]
                        cache.put(tile, size, mtime, counts)
                        counts_by_path[tile] = counts
                    completed += 1
                    if on_progress:
                        on_progress(completed, total)

    for folder, tiles in tiles_by_folder.items():
        cache.prune_missing(folder, {str(t) for t in tiles})
    cache.save()

    return {
        folder: _build_result(tiles, counts_by_path, failed)
        for folder, tiles in tiles_by_folder.items()
    }


def tile_breakdown(scenery_pack_dir: Path, kind: str, type_name: str) -> list[TileCount]:
    """Per-tile active/disabled counts for one type within one folder - a
    testing/debugging aid, so it only reads already-cached data (ScanCache's
    per-tile counts + any .xwcdisabled sidecars), no decompiling. That makes
    it fast regardless of folder size, and it's always safe to call: a type
    only ever appears in the tree after its folder has been scanned at least
    once, so its tiles are guaranteed to already be in the cache. Tiles with
    zero instances of this type (the vast majority, usually) are omitted."""
    cache = ScanCache()
    results: list[TileCount] = []
    for tile in _list_tiles(scenery_pack_dir):
        st = tile.stat()
        cached = cache.get(tile, st.st_size, st.st_mtime)
        active = cached.get(kind, {}).get(type_name, 0) if cached else 0

        disabled = 0
        if has_disabled_records(tile):
            disabled = len(load_disabled_records(tile).get(type_name, []))

        if active or disabled:
            results.append(TileCount(tile, active, disabled))

    results.sort(key=lambda tc: -(tc.active_count + tc.disabled_count))
    return results


def _list_tiles(scenery_pack_dir: Path) -> list[Path]:
    nav_data_dir = scenery_pack_dir / "Earth nav data"
    return sorted(nav_data_dir.glob("**/*.dsf")) if nav_data_dir.is_dir() else []


def _build_result(
    tiles: list[Path],
    counts_by_path: dict[Path, dict[str, dict[str, int]]],
    failed: set[Path],
) -> ScanResult:
    active_totals: dict[str, dict[str, int]] = {kind: {} for kind in _KINDS}
    disabled_totals: dict[str, dict[str, int]] = {kind: {} for kind in _KINDS}
    failed_tiles: list[Path] = []

    for tile in tiles:
        if tile in failed:
            failed_tiles.append(tile)
            continue
        _merge(active_totals, counts_by_path.get(tile, _EMPTY_COUNTS))

        if has_disabled_records(tile):
            for type_name, instances in load_disabled_records(tile).items():
                if not instances:
                    continue
                kind = kind_of_line(instances[0][0])
                disabled_totals[kind][type_name] = disabled_totals[kind].get(type_name, 0) + len(instances)

    type_counts = []
    for kind in _KINDS:
        names = set(active_totals[kind]) | set(disabled_totals[kind])
        for name in names:
            active = active_totals[kind].get(name, 0)
            disabled = disabled_totals[kind].get(name, 0)
            type_counts.append(TypeCount(name, kind, active, disabled))

    type_counts.sort(key=lambda c: (c.kind, -(c.active_count + c.disabled_count), c.type_name))
    return ScanResult(type_counts, failed_tiles)


def _scan_tile(path: Path, tmp_dir: Path) -> dict[str, dict[str, int]]:
    text_path = tmp_dir / f"{uuid.uuid4().hex}.txt"
    try:
        decompile(path, text_path)
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

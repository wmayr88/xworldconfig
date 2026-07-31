"""Builds per-folder, per-type instance counts by decompiling every tile in
one or more scenery pack folders, for display in the object-type tree.

This intentionally does not go through xworldconfig.dsf.text_model - that
model retains every instance's exact source lines (needed later by apply.py
to reconstruct/filter a tile), which is far more memory than a folder-wide
count needs. A single simHeaven footprints tile can decompile to several
million lines, so this does a single streaming pass per tile and only keeps
running counts, never the instance lines themselves.

Each type's count is split into active (currently present in the live tile)
and disabled (present in the .xwcorig backup - see xworldconfig.dsf.backup -
but no longer in the live tile, i.e. filtered out by xworldconfig.dsf.apply).
Since no tile has a backup until apply.py actually disables something, this
correctly reports 0 disabled everywhere today without any special-casing;
it becomes accurate automatically once apply.py exists.

Unchanged files (by size + mtime) are served from xworldconfig.dsf.scan_cache
instead of being re-decompiled. scan_folders() flattens every tile (and any
existing backup) across ALL requested folders into one shared thread pool
and one shared progress count, rather than a separate pool per folder - this
avoids the tail-end underutilization of re-spinning a pool per folder when
scanning many folders at once, and gives a single, meaningful (completed,
total) progress signal for a "scan everything" operation. scan_folder() is a
thin single-folder wrapper around it, used for the lazy per-category scan
triggered by expanding a tree item."""
import concurrent.futures
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from xworldconfig.dsf.backup import backup_path
from xworldconfig.dsf.dsftool import DSFToolError, decompile
from xworldconfig.dsf.scan_cache import ScanCache

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


def scan_folder(scenery_pack_dir: Path, max_workers: int | None = None) -> ScanResult:
    return scan_folders([scenery_pack_dir], max_workers=max_workers)[scenery_pack_dir]


def scan_folders(
    scenery_pack_dirs: list[Path],
    max_workers: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[Path, ScanResult]:
    tiles_by_folder = {d: _list_tiles(d) for d in scenery_pack_dirs}

    tile_backup: dict[Path, Path | None] = {}
    all_paths: set[Path] = set()
    for tiles in tiles_by_folder.values():
        for tile in tiles:
            all_paths.add(tile)
            backup = backup_path(tile)
            tile_backup[tile] = backup if backup.exists() else None
            if tile_backup[tile] is not None:
                all_paths.add(tile_backup[tile])

    cache = ScanCache()
    stats: dict[Path, tuple[int, float]] = {}
    counts_by_path: dict[Path, dict[str, dict[str, int]]] = {}
    to_scan: list[Path] = []
    failed: set[Path] = set()

    for path in all_paths:
        st = path.stat()
        stats[path] = (st.st_size, st.st_mtime)
        cached = cache.get(path, st.st_size, st.st_mtime)
        if cached is not None:
            counts_by_path[path] = cached
        else:
            to_scan.append(path)

    total = len(all_paths)
    completed = total - len(to_scan)
    if on_progress:
        on_progress(completed, total)

    if to_scan:
        workers = max_workers or (os.cpu_count() or 4)
        with tempfile.TemporaryDirectory(prefix="xworldconfig-scan-") as tmp_dir:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_scan_tile, path, Path(tmp_dir)): path for path in to_scan}
                for future in concurrent.futures.as_completed(futures):
                    path = futures[future]
                    try:
                        counts = future.result()
                    except DSFToolError:
                        failed.add(path)
                        counts_by_path[path] = _EMPTY_COUNTS
                    else:
                        size, mtime = stats[path]
                        cache.put(path, size, mtime, counts)
                        counts_by_path[path] = counts
                    completed += 1
                    if on_progress:
                        on_progress(completed, total)

    for folder, tiles in tiles_by_folder.items():
        existing = {str(t) for t in tiles}
        existing |= {str(tile_backup[t]) for t in tiles if tile_backup[t] is not None}
        cache.prune_missing(folder, existing)
    cache.save()

    return {
        folder: _build_result(tiles, tile_backup, counts_by_path, failed)
        for folder, tiles in tiles_by_folder.items()
    }


def _list_tiles(scenery_pack_dir: Path) -> list[Path]:
    nav_data_dir = scenery_pack_dir / "Earth nav data"
    return sorted(nav_data_dir.glob("**/*.dsf")) if nav_data_dir.is_dir() else []


def _build_result(
    tiles: list[Path],
    tile_backup: dict[Path, Path | None],
    counts_by_path: dict[Path, dict[str, dict[str, int]]],
    failed: set[Path],
) -> ScanResult:
    active_totals: dict[str, dict[str, int]] = {kind: {} for kind in _KINDS}
    original_totals: dict[str, dict[str, int]] = {kind: {} for kind in _KINDS}
    failed_tiles: list[Path] = []

    for tile in tiles:
        if tile in failed:
            failed_tiles.append(tile)
            continue
        live = counts_by_path.get(tile, _EMPTY_COUNTS)
        _merge(active_totals, live)
        backup = tile_backup[tile]
        original = counts_by_path.get(backup, live) if backup is not None else live
        _merge(original_totals, original)

    type_counts = []
    for kind in _KINDS:
        names = set(active_totals[kind]) | set(original_totals[kind])
        for name in names:
            active = active_totals[kind].get(name, 0)
            original = original_totals[kind].get(name, 0)
            disabled = max(0, original - active)
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

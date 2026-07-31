"""The single write path for every DSF mutation: decompile the live tile
(always the source of truth for what's currently enabled), merge in whatever
the .xwcdisabled sidecar currently holds (what's currently disabled), then
recompile with exactly the folder's current disabled-type set commented out
via xworldconfig.dsf.text_model - producing a definitive instance list every
time rather than incrementally patching an already-modified live file. This
is what prevents re-toggling a type from ever introducing duplicate
instances: a type's instances only ever come from one place (live text or
sidecar), and every write recomputes the full result from both sources.

Mirrors xworldconfig.dsf.inventory's scan_folder/scan_folders shape: a
shared thread pool across every tile (in every given folder, for
apply_folders), the same worker-count sizing, and an (completed, total)
progress callback for a modal progress dialog to render."""
import concurrent.futures
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from xworldconfig.dsf.backup import HashStore, hash_file, load_disabled_records, save_disabled_records
from xworldconfig.dsf.concurrency import default_worker_count
from xworldconfig.dsf.dsftool import DSFToolError, compile_text, decompile
from xworldconfig.dsf.text_model import DsfText, Instance, kind_of_line, parse, render

_DEF_KIND_FOR_INSTANCE_KIND = {"OBJECT": "OBJECT_DEF", "POLYGON": "POLYGON_DEF", "NETWORK": "NETWORK_DEF"}


@dataclass
class ApplyResult:
    touched: int  # tiles rewritten, still have at least one disabled type afterward
    restored: int  # tiles rewritten, nothing disabled there anymore (sidecar removed)
    failed_tiles: list[Path]


def apply_folder(
    scenery_pack_dir: Path,
    disabled_type_names: set[str],
    max_workers: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> ApplyResult:
    return apply_folders(
        [(scenery_pack_dir, disabled_type_names)], max_workers=max_workers, on_progress=on_progress
    )[scenery_pack_dir]


def apply_folders(
    folder_configs: list[tuple[Path, set[str]]],
    max_workers: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[Path, ApplyResult]:
    tiles_by_folder = {folder: _list_tiles(folder) for folder, _ in folder_configs}
    disabled_names_by_folder = dict(folder_configs)

    tile_folder: dict[Path, Path] = {}
    for folder, tiles in tiles_by_folder.items():
        for tile in tiles:
            tile_folder[tile] = folder

    total = len(tile_folder)
    completed = 0
    if on_progress:
        on_progress(completed, total)

    status_by_tile: dict[Path, str] = {}
    hash_store = HashStore()

    if tile_folder:
        workers = max_workers or default_worker_count()
        with tempfile.TemporaryDirectory(prefix="xworldconfig-apply-") as tmp_dir:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_apply_tile, tile, disabled_names_by_folder[folder], Path(tmp_dir)): tile
                    for tile, folder in tile_folder.items()
                }
                for future in concurrent.futures.as_completed(futures):
                    tile = futures[future]
                    try:
                        status, new_hash = future.result()
                    except (DSFToolError, OSError):
                        status_by_tile[tile] = "failed"
                    else:
                        status_by_tile[tile] = status
                        if new_hash is None:
                            hash_store.forget(tile)
                        else:
                            hash_store.record(tile, new_hash)
                    completed += 1
                    if on_progress:
                        on_progress(completed, total)

    hash_store.save()

    results: dict[Path, ApplyResult] = {}
    for folder, tiles in tiles_by_folder.items():
        touched = sum(1 for t in tiles if status_by_tile.get(t) == "touched")
        restored = sum(1 for t in tiles if status_by_tile.get(t) == "restored")
        failed_tiles = [t for t in tiles if status_by_tile.get(t) == "failed"]
        results[folder] = ApplyResult(touched, restored, failed_tiles)
    return results


def _list_tiles(scenery_pack_dir: Path) -> list[Path]:
    nav_data_dir = scenery_pack_dir / "Earth nav data"
    return sorted(nav_data_dir.glob("**/*.dsf")) if nav_data_dir.is_dir() else []


def _apply_tile(tile: Path, disabled_type_names: set[str], tmp_dir: Path) -> tuple[str, str | None]:
    text_path = tmp_dir / f"{uuid.uuid4().hex}.txt"
    try:
        decompile(tile, text_path)
        dsf_text = parse(text_path.read_text(encoding="utf-8"))
    finally:
        text_path.unlink(missing_ok=True)

    records = load_disabled_records(tile)
    def_names = {d.resource_path for d in dsf_text.defs}
    if not records and not (def_names & disabled_type_names):
        return "untouched", None

    restored_instances = _records_to_instances(records, dsf_text.defs)
    combined = DsfText(dsf_text.header_lines, dsf_text.defs, dsf_text.instances + restored_instances)

    rendered = render(combined, disabled_type_names)
    rendered_text_path = tmp_dir / f"{uuid.uuid4().hex}.txt"
    rendered_text_path.write_text(rendered, encoding="utf-8")
    compiled_tmp_path = tile.with_name(f"{tile.name}.xwctmp")
    try:
        compile_text(rendered_text_path, compiled_tmp_path)
        os.replace(compiled_tmp_path, tile)
    finally:
        rendered_text_path.unlink(missing_ok=True)
        compiled_tmp_path.unlink(missing_ok=True)

    new_records: dict[str, list[list[str]]] = {}
    for inst in combined.instances:
        name = combined.type_name(inst.kind, inst.def_index)
        if name in disabled_type_names:
            new_records.setdefault(name, []).append(inst.raw_lines)
    save_disabled_records(tile, new_records)

    new_hash = hash_file(tile)
    status = "touched" if new_records else "restored"
    return status, new_hash


def _records_to_instances(records: dict[str, list[list[str]]], defs: list) -> list[Instance]:
    def_lookup = {(d.kind, d.resource_path): d.index for d in defs}
    instances: list[Instance] = []
    for type_name, instance_line_lists in records.items():
        for raw_lines in instance_line_lists:
            kind = kind_of_line(raw_lines[0])
            def_kind = _DEF_KIND_FOR_INSTANCE_KIND[kind]
            index = def_lookup.get((def_kind, type_name))
            if index is None:
                # def no longer present in this tile version - stale sidecar entry
                # from an update the startup drift check should have already
                # caught; drop it defensively rather than fail the whole tile.
                continue
            instances.append(Instance(kind, index, raw_lines))
    return instances

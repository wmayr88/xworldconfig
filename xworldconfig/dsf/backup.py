"""Manages the .xwcdisabled sidecar for each tile that currently has at least
one disabled object/polygon/network type, and tracks the hash we expect the
live tile to currently have so drift (an external change, almost always a
simHeaven update) can be caught cheaply at app startup without re-decompiling.

The sidecar is NOT a backup of the original tile - the install this app
manages is already 100+ GB and only grows, so a full pristine copy per
touched tile was rejected as the wrong trade. It stores only the minority
"disabled" content: for each disabled type, the exact raw source lines of
every instance that was removed (single line for OBJECT, the whole block for
POLYGON/NETWORK) - reusing xworldconfig.dsf.text_model's block-scanning so a
POLYGON/NETWORK instance's internal lines are never split incorrectly. This
is what apply.py splices back in when a type is re-enabled.

Text format (gzip-compressed on disk - this kind of repetitive, keyword-
prefixed DSF text compresses to roughly a quarter of its size):

    #TYPE simheaven/details/bench.obj
    OBJECT 5 28.218572137 -15.428931106 148.998856
    OBJECT 5 28.214122225 -15.429823758 148.998856
    #TYPE simheaven/farms/farm_10x15.obj
    BEGIN_POLYGON 3 2
    BEGIN_WINDING
    POLYGON_POINT 31.757557031 30.558108263
    END_WINDING
    END_POLYGON

No JSON: a `#TYPE` marker per group, then each instance's raw lines exactly
as they appeared in the tile. Def-table indices (the "5" above) don't need
separate tracking - they're embedded in the raw line text and stay valid
forever since the definition table is never trimmed."""
import gzip
import hashlib
import json
from pathlib import Path

from xworldconfig.dsf.text_model import _consume_instance
from xworldconfig.paths import tile_hash_store_path

DISABLED_SUFFIX = ".xwcdisabled"
_TYPE_MARKER_PREFIX = "#TYPE "


def disabled_records_path(dsf_path: Path) -> Path:
    return dsf_path.with_name(dsf_path.name + DISABLED_SUFFIX)


def has_disabled_records(dsf_path: Path) -> bool:
    return disabled_records_path(dsf_path).exists()


def load_disabled_records(dsf_path: Path) -> dict[str, list[list[str]]]:
    path = disabled_records_path(dsf_path)
    if not path.exists():
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return _parse_records_text(f.read())


def save_disabled_records(dsf_path: Path, records: dict[str, list[list[str]]]) -> None:
    path = disabled_records_path(dsf_path)
    if not records:
        path.unlink(missing_ok=True)
        return
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(_render_records_text(records))


def delete_disabled_records(dsf_path: Path) -> None:
    disabled_records_path(dsf_path).unlink(missing_ok=True)


def _render_records_text(records: dict[str, list[list[str]]]) -> str:
    lines: list[str] = []
    for type_name, instances in records.items():
        lines.append(f"{_TYPE_MARKER_PREFIX}{type_name}")
        for instance_lines in instances:
            lines.extend(instance_lines)
    return "\n".join(lines) + "\n"


def _parse_records_text(text: str) -> dict[str, list[list[str]]]:
    lines = text.splitlines()
    records: dict[str, list[list[str]]] = {}
    current_type: str | None = None
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.startswith(_TYPE_MARKER_PREFIX):
            current_type = line[len(_TYPE_MARKER_PREFIX):]
            records.setdefault(current_type, [])
            i += 1
            continue
        instance, i = _consume_record_instance(lines, i)
        records[current_type].append(instance)
    return records


def _consume_record_instance(lines: list[str], i: int) -> tuple[list[str], int]:
    line = lines[i]
    if line.startswith("BEGIN_POLYGON "):
        return _consume_instance(lines, i, "END_POLYGON")
    if line.startswith("BEGIN_SEGMENT "):
        return _consume_instance(lines, i, "END_SEGMENT")
    return [line], i + 1


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class HashStore:
    """Tracks the hash we expect each live tile to currently have, persisted
    next to the executable. Loaded once and mutated in memory (mirroring
    xworldconfig.dsf.scan_cache.ScanCache) so a concurrent apply_folders()
    batch touching many tiles doesn't race on repeated load-modify-save of
    the same JSON file - callers should record from the single-threaded
    completion loop, then call save() once at the end."""

    def __init__(self):
        self._hashes: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        path = tile_hash_store_path()
        if not path.exists():
            return
        try:
            self._hashes = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._hashes = {}

    def expected_hash(self, dsf_path: Path) -> str | None:
        return self._hashes.get(str(dsf_path))

    def record(self, dsf_path: Path, hash_value: str) -> None:
        self._hashes[str(dsf_path)] = hash_value

    def forget(self, dsf_path: Path) -> None:
        self._hashes.pop(str(dsf_path), None)

    def detect_drift(self, dsf_path: Path) -> bool:
        expected = self.expected_hash(dsf_path)
        if expected is None:
            return False
        return hash_file(dsf_path) != expected

    def save(self) -> None:
        tile_hash_store_path().write_text(json.dumps(self._hashes), encoding="utf-8")

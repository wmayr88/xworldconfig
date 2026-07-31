"""Reads and writes scenery_packs.ini, toggling SCENERY_PACK <->
SCENERY_PACK_DISABLED for individual folders while preserving line order and
every other line (header, non-simHeaven packs) untouched. This file is the
sole source of truth for whole-folder enabled/disabled state."""
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SceneryPackEntry:
    line_index: int
    folder_path: str  # e.g. "Custom Scenery/simHeaven_X-World_Africa-3-details/"
    enabled: bool


class SceneryPacksIni:
    _ENABLED = "SCENERY_PACK "
    _DISABLED = "SCENERY_PACK_DISABLED "

    def __init__(self, path: Path):
        self.path = path
        self._lines: list[str] = []
        self.entries: list[SceneryPackEntry] = []
        self._load()

    def _load(self) -> None:
        self._lines = self.path.read_text(encoding="utf-8").splitlines()
        self.entries = []
        for i, line in enumerate(self._lines):
            if line.startswith(self._ENABLED):
                self.entries.append(SceneryPackEntry(i, line[len(self._ENABLED):].strip(), True))
            elif line.startswith(self._DISABLED):
                self.entries.append(SceneryPackEntry(i, line[len(self._DISABLED):].strip(), False))

    def find_by_folder_name(self, folder_name: str) -> SceneryPackEntry | None:
        for entry in self.entries:
            if entry.folder_path.rstrip("/").split("/")[-1] == folder_name:
                return entry
        return None

    def set_enabled(self, entry: SceneryPackEntry, enabled: bool) -> None:
        prefix = self._ENABLED if enabled else self._DISABLED
        self._lines[entry.line_index] = f"{prefix}{entry.folder_path}"
        entry.enabled = enabled

    def save(self) -> None:
        self.path.write_text("\n".join(self._lines) + "\n", encoding="utf-8")

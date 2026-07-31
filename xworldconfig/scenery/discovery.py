"""Scans a Custom Scenery directory for simHeaven X-World folders under the
freeware and Pro prefixes (each user-configurable, defaulting to simHeaven's
own naming) and correlates each one against scenery_packs.ini.

Folder names are parsed generically as `<region>-<sequence>-<category>`,
optionally followed by `_<variant>` (simHeaven ships a couple of categories,
e.g. Pro's "bridges", in mesh-specific variants like `_O4XP-Mesh`). No fixed
category vocabulary is hardcoded - freeware and Pro use different category
sets, not every region has every category (Antarctica, Pro-only, is missing
several), and this keeps discovery forward-compatible with future simHeaven
releases without an update. Folders that don't fit the pattern at all (e.g.
the shared `..._Library` folders) are still returned, just without
region/sequence/category/variant, so they remain toggleable in the
scenery-pack list even though they won't participate in per-region grouping.

This module never edits scenery_packs.ini or reorders/creates entries in it -
see xworldconfig.ini_parser. A folder that exists on disk but has no
corresponding entry (ini_entry is None) means X-Plane hasn't been launched
since the pack was installed; the caller should prompt the user to launch
X-Plane once (to let it register the folder) and rescan, not attempt to add
the line itself."""
import re
from dataclasses import dataclass
from pathlib import Path

from xworldconfig.ini_parser import SceneryPackEntry, SceneryPacksIni

DEFAULT_FREEWARE_PREFIX = "simHeaven_X-World_"
DEFAULT_PRO_PREFIX = "simHeaven_X-WORLD-Pro_"

_NAME_PATTERN = re.compile(r"^(?P<region>.+)-(?P<sequence>\d+)-(?P<category>[^_]+)(?:_(?P<variant>.+))?$")


@dataclass
class SceneryFolder:
    path: Path
    edition: str  # "freeware" | "pro"
    region: str | None
    sequence: int | None
    category: str | None
    variant: str | None
    ini_entry: SceneryPackEntry | None  # None means not yet registered in scenery_packs.ini


def discover(
    custom_scenery_dir: Path,
    freeware_prefix: str,
    pro_prefix: str,
    ini: SceneryPacksIni | None,
) -> list[SceneryFolder]:
    folders = []
    for entry in sorted(custom_scenery_dir.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        edition, remainder = _match_prefix(entry.name, freeware_prefix, pro_prefix)
        if edition is None:
            continue
        region, sequence, category, variant = _parse_name(remainder)
        ini_entry = ini.find_by_folder_name(entry.name) if ini else None
        folders.append(SceneryFolder(entry, edition, region, sequence, category, variant, ini_entry))
    return folders


def _match_prefix(folder_name: str, freeware_prefix: str, pro_prefix: str) -> tuple[str | None, str]:
    lower = folder_name.lower()
    if pro_prefix and lower.startswith(pro_prefix.lower()):
        return "pro", folder_name[len(pro_prefix):]
    if freeware_prefix and lower.startswith(freeware_prefix.lower()):
        return "freeware", folder_name[len(freeware_prefix):]
    return None, folder_name


def _parse_name(remainder: str) -> tuple[str | None, int | None, str | None, str | None]:
    match = _NAME_PATTERN.match(remainder)
    if not match:
        return None, None, None, None
    return (
        match.group("region"),
        int(match.group("sequence")),
        match.group("category"),
        match.group("variant"),
    )

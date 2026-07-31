"""Scans a Custom Scenery directory for simHeaven X-World folders under the
freeware and Pro prefixes (each user-configurable, defaulting to simHeaven's
own naming), grouping results by edition, region (continent), and category
(vfr/regions/details/extras/footprints/scenery/forests/network)."""
from dataclasses import dataclass
from pathlib import Path

DEFAULT_FREEWARE_PREFIX = "simHeaven_X-World_"
DEFAULT_PRO_PREFIX = "simHeaven_X-WORLD-Pro_"


@dataclass
class SceneryFolder:
    path: Path
    edition: str  # "freeware" | "pro"
    region: str  # e.g. "Africa"
    category: str  # e.g. "details"


def discover(custom_scenery_dir: Path, freeware_prefix: str, pro_prefix: str) -> list[SceneryFolder]:
    raise NotImplementedError

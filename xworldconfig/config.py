"""Loads and saves the app's portable settings file, which lives next to the
executable (see xworldconfig.paths.config_file_path) rather than in an OS
app-data directory. scenery_packs.ini remains the source of truth for whole
folder enabled/disabled state; this file only stores the custom scenery
location, edition prefixes, and per-folder disabled object-type choices."""
import json
from dataclasses import asdict, dataclass, field

from xworldconfig.paths import config_file_path
from xworldconfig.scenery.discovery import DEFAULT_FREEWARE_PREFIX, DEFAULT_PRO_PREFIX


@dataclass
class Settings:
    custom_scenery_dir: str = ""
    freeware_prefix: str = DEFAULT_FREEWARE_PREFIX
    pro_prefix: str = DEFAULT_PRO_PREFIX
    # folder name -> list of disabled type names for that folder
    disabled_types: dict = field(default_factory=dict)


def load() -> Settings:
    path = config_file_path()
    if not path.exists():
        return Settings()
    data = json.loads(path.read_text(encoding="utf-8"))
    return Settings(**data)


def save(settings: Settings) -> None:
    config_file_path().write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")

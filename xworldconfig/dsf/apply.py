"""The single write path for every DSF mutation: backup (canonical full
source) -> filter by the currently configured disabled type names -> compile
-> overwrite the live file. Never patches an already-modified live file
incrementally, so re-toggling a type can never introduce duplicate
instances."""
from pathlib import Path


def apply_folder(scenery_pack_dir: Path, disabled_type_names: set[str]) -> None:
    raise NotImplementedError

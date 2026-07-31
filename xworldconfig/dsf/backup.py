"""Manages the .xwcorig sibling backup for each touched tile (a pristine copy
of the .dsf taken the first time it's ever modified) and detects drift -
cases where the live file no longer matches what this tool last wrote,
almost always because simHeaven was updated/reinstalled."""
from pathlib import Path

BACKUP_SUFFIX = ".xwcorig"


def backup_path(dsf_path: Path) -> Path:
    return dsf_path.with_name(dsf_path.name + BACKUP_SUFFIX)


def has_backup(dsf_path: Path) -> bool:
    return backup_path(dsf_path).exists()


def create_backup(dsf_path: Path) -> None:
    raise NotImplementedError


def hash_file(path: Path) -> str:
    raise NotImplementedError


def detect_drift(dsf_path: Path, expected_hash: str) -> bool:
    raise NotImplementedError

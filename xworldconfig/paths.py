"""Resolves filesystem locations for the portable app: its own root directory
(next to the running executable, not an OS app-data path), the bundled
utilities/ folder, the per-OS DSFTool binary, and the settings file."""
import platform
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def app_root() -> Path:
    # Memoized: it's a process-lifetime constant, and apply.py's concurrent
    # worker threads all call this (via dsftool_path()) on every DSFTool
    # invocation - repeatedly resolving it under heavy concurrency was
    # observed to race CPython's internal realpath symlink-resolution cache
    # and raise a spurious RecursionError, beyond just being wasted work.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def utilities_dir() -> Path:
    return app_root() / "utilities"


def dsftool_path() -> Path:
    system = platform.system()
    if system == "Windows":
        return utilities_dir() / "win" / "DSFTool.exe"
    if system == "Darwin":
        return utilities_dir() / "mac" / "DSFTool"
    return utilities_dir() / "lin" / "DSFTool"


def config_file_path() -> Path:
    return app_root() / "xworldconfig.json"


def scan_cache_path() -> Path:
    return app_root() / "xworldconfig_cache.json"


def tile_hash_store_path() -> Path:
    return app_root() / "xworldconfig_hashes.json"

"""Resolves filesystem locations for the portable app: its own root directory
(next to the running executable, not an OS app-data path), the bundled
utilities/ folder, the per-OS DSFTool binary, and the settings file."""
import platform
import sys
from pathlib import Path


def app_root() -> Path:
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

"""Subprocess wrapper around the platform-specific DSFTool binary for
decompiling .dsf files to DSFTool's text dialect and back."""
import subprocess
from pathlib import Path

from xworldconfig.paths import dsftool_path


class DSFToolError(RuntimeError):
    pass


def decompile(dsf_path: Path, text_path: Path) -> None:
    _run("--dsf2text", dsf_path, text_path)


def compile_text(text_path: Path, dsf_path: Path) -> None:
    _run("--text2dsf", text_path, dsf_path)


def _run(flag: str, src: Path, dst: Path) -> None:
    result = subprocess.run(
        [str(dsftool_path()), flag, str(src), str(dst)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DSFToolError(f"DSFTool {flag} failed for {src}:\n{result.stdout}\n{result.stderr}")

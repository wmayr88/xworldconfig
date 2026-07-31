"""Shared worker-count sizing for anything that runs DSFTool subprocesses
concurrently (xworldconfig.dsf.inventory's scanning, xworldconfig.dsf.apply's
write path)."""
import os


def default_worker_count() -> int:
    """Leaves the rest of the machine some headroom instead of saturating
    every core with DSFTool subprocesses - 2 cores free on smaller machines,
    4 free on larger ones - while always leaving at least 1 worker."""
    cpu_count = os.cpu_count() or 4
    reserved = 4 if cpu_count > 8 else 2
    return max(1, cpu_count - reserved)

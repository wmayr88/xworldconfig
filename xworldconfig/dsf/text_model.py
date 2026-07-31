"""Parses DSFTool's decompiled text format into structured definitions and
instances, and re-serializes it with disabled instances commented out (rather
than deleted) so DSFTool's --text2dsf silently drops them on compile.

Handles both entry shapes: single-line (OBJECT) and block-delimited
(BEGIN_POLYGON...END_POLYGON, BEGIN_SEGMENT...END_SEGMENT). Definition
tables (OBJECT_DEF/POLYGON_DEF/NETWORK_DEF) are always preserved in full,
even at zero enabled instances, so indices stay stable across edits."""
from dataclasses import dataclass

DISABLED_MARKER = "#XWORLDCONFIG_DISABLED# "


@dataclass
class DefEntry:
    kind: str  # "OBJECT_DEF" | "POLYGON_DEF" | "NETWORK_DEF"
    index: int
    resource_path: str  # e.g. "simheaven/details/bench.obj"


@dataclass
class Instance:
    kind: str  # "OBJECT" | "POLYGON" | "NETWORK"
    def_index: int
    raw_lines: list[str]


@dataclass
class DsfText:
    header_lines: list[str]
    defs: list[DefEntry]
    instances: list[Instance]

    def type_name(self, kind: str, def_index: int) -> str:
        raise NotImplementedError

    def instance_counts_by_type(self) -> dict[str, int]:
        raise NotImplementedError


def parse(text: str) -> DsfText:
    raise NotImplementedError


def render(dsf_text: DsfText, disabled_type_names: set[str]) -> str:
    raise NotImplementedError

"""Parses DSFTool's decompiled text format into structured definitions and
instances, and re-serializes it with disabled instances commented out (rather
than deleted) so DSFTool's --text2dsf silently drops them on compile.

Handles both entry shapes: single-line (OBJECT) and block-delimited
(BEGIN_POLYGON...END_POLYGON, BEGIN_SEGMENT...END_SEGMENT). Definition
tables (OBJECT_DEF/POLYGON_DEF/NETWORK_DEF) are always preserved in full,
even at zero enabled instances, so indices stay stable across edits.

_consume_instance() is exposed (not underscored-private in spirit, just in
this module) because xworldconfig.dsf.backup's removed-records sidecar
format reuses the exact same block-scanning logic for its own instance
groups."""
from dataclasses import dataclass

DISABLED_MARKER = "#XWORLDCONFIG_DISABLED# "

_DEF_PREFIXES = {
    "OBJECT_DEF ": "OBJECT_DEF",
    "POLYGON_DEF ": "POLYGON_DEF",
    "NETWORK_DEF ": "NETWORK_DEF",
}
_BLOCK_START_TO_KIND_AND_END = {
    "BEGIN_POLYGON ": ("POLYGON", "END_POLYGON"),
    "BEGIN_SEGMENT ": ("NETWORK", "END_SEGMENT"),
}
_DEF_KIND_FOR_INSTANCE_KIND = {
    "OBJECT": "OBJECT_DEF",
    "POLYGON": "POLYGON_DEF",
    "NETWORK": "NETWORK_DEF",
}


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
        def_kind = _DEF_KIND_FOR_INSTANCE_KIND[kind]
        for d in self.defs:
            if d.kind == def_kind and d.index == def_index:
                return d.resource_path
        return f"<unknown index {def_index}>"

    def instance_counts_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for inst in self.instances:
            name = self.type_name(inst.kind, inst.def_index)
            counts[name] = counts.get(name, 0) + 1
        return counts


def parse(text: str) -> DsfText:
    lines = text.splitlines()
    header_lines: list[str] = []
    defs: list[DefEntry] = []
    instances: list[Instance] = []
    def_counts = {"OBJECT_DEF": 0, "POLYGON_DEF": 0, "NETWORK_DEF": 0}
    seen_first_def = False

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        matched_def_kind = None
        for prefix, kind in _DEF_PREFIXES.items():
            if line.startswith(prefix):
                matched_def_kind = kind
                resource_path = line[len(prefix):].strip()
                break
        if matched_def_kind is not None:
            seen_first_def = True
            index = def_counts[matched_def_kind]
            defs.append(DefEntry(matched_def_kind, index, resource_path))
            def_counts[matched_def_kind] += 1
            i += 1
            continue

        if line.startswith("OBJECT "):
            index = int(line.split(" ", 2)[1])
            instances.append(Instance("OBJECT", index, [line]))
            i += 1
            continue

        matched_block = None
        for prefix, (kind, end_marker) in _BLOCK_START_TO_KIND_AND_END.items():
            if line.startswith(prefix):
                matched_block = (kind, end_marker)
                break
        if matched_block is not None:
            kind, end_marker = matched_block
            index = int(line.split(" ", 2)[1])
            instance, i = _consume_instance(lines, i, end_marker)
            instances.append(Instance(kind, index, instance))
            continue

        if not seen_first_def:
            header_lines.append(line)
        i += 1

    return DsfText(header_lines, defs, instances)


def kind_of_line(line: str) -> str:
    """Which instance kind a raw source line belongs to, from its own text -
    shared by inventory.py (counting) and apply.py (moving instances between
    the live tile and the .xwcdisabled sidecar)."""
    if line.startswith("BEGIN_POLYGON "):
        return "POLYGON"
    if line.startswith("BEGIN_SEGMENT "):
        return "NETWORK"
    return "OBJECT"


def _consume_instance(lines: list[str], start: int, end_marker: str) -> tuple[list[str], int]:
    """Collects lines[start:] through the line starting with end_marker
    (inclusive), returning (block_lines, index_after_block). Used for both
    the main DSF text (BEGIN_POLYGON/BEGIN_SEGMENT blocks) and the
    .xwcdisabled sidecar format (which stores the same raw blocks)."""
    block = [lines[start]]
    i = start + 1
    n = len(lines)
    while i < n and not lines[i].startswith(end_marker):
        block.append(lines[i])
        i += 1
    if i < n:
        block.append(lines[i])
        i += 1
    return block, i


def render(dsf_text: DsfText, disabled_type_names: set[str]) -> str:
    lines: list[str] = list(dsf_text.header_lines)

    for def_kind in ("OBJECT_DEF", "POLYGON_DEF", "NETWORK_DEF"):
        for d in sorted((d for d in dsf_text.defs if d.kind == def_kind), key=lambda d: d.index):
            lines.append(f"{def_kind} {d.resource_path}")

    name_lookup = {(d.kind, d.index): d.resource_path for d in dsf_text.defs}
    for inst in dsf_text.instances:
        def_kind = _DEF_KIND_FOR_INSTANCE_KIND[inst.kind]
        name = name_lookup.get((def_kind, inst.def_index), "")
        if name in disabled_type_names:
            lines.extend(f"{DISABLED_MARKER}{line}" for line in inst.raw_lines)
        else:
            lines.extend(inst.raw_lines)

    return "\n".join(lines) + "\n"

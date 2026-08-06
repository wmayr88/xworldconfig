# xworldconfig

A portable Python/PySide6 GUI for managing simHeaven X-World scenery in X-Plane 12: enabling/disabling whole scenery-pack folders via `scenery_packs.ini`, and enabling/disabling individual object/polygon/network types within a folder's DSF tiles.

## What it does today

- Discovers simHeaven scenery folders under a user-chosen `Custom Scenery` directory, for both the freeware (`simHeaven_X-World_`) and Pro (`simHeaven_X-WORLD-Pro_`) editions (prefixes are configurable in the UI, in case someone renamed folders).
- Correlates each folder against `scenery_packs.ini` and lets the user toggle whole-folder enable/disable (writes `SCENERY_PACK`/`SCENERY_PACK_DISABLED` directly). Folders found on disk but missing from the ini (X-Plane hasn't been launched since install) hard-block the tree with a popup until resolved.
- Lazily decompiles a folder's DSF tiles (via DSFTool) to show a per-object-type inventory with active/disabled counts, cached on disk so repeat scans are near-instant. A "Scan All Objects" button does this across every enabled folder at once, in parallel, with a text-based progress bar.
- **Actually disables/enables individual object/polygon/network types**, per folder or in bulk across every same-category folder in the same edition (region right-click, category right-click, type right-click, or type-group right-click) - this is the core feature and it's fully wired end to end: checkbox -> `apply.py` -> real DSF rewrite -> `.xwcdisabled` sidecar -> tree updates.
- Detects drift at startup: if a folder with disabled-type configuration has changed on disk since this app last wrote it (almost always a simHeaven update), a modal makes the user choose "apply existing configuration" or "reset to enabled" before the tree is ever shown - never silently reapplies, never leaves the UI and the files disagreeing.

## Non-negotiable ground rules (from the user)

- **Never reorder, add, or remove lines in `scenery_packs.ini`** beyond flipping `SCENERY_PACK` ↔ `SCENERY_PACK_DISABLED` for an *existing* line. Load-order management is explicitly out of scope - other tools already do that. If a folder exists on disk but has no line in the ini yet, the app must not add one; it must tell the user to launch X-Plane once (which writes it) and rescan. See `ini_parser.py` / `discovery.py`.
- **Freeware and Pro are fully separate configuration domains.** Never let a bulk action or scope selection cross between them, even if both are installed simultaneously. Bulk type-toggle scope (`_apply_type_names_everywhere` in `main_window.py`) matches on both `edition` and `category`, never just `category`.
- **Never take the destructive path when a non-destructive one exists.** The whole design of the disable mechanism (below) exists specifically to avoid ever losing data irreversibly.

## The disable mechanism (implemented)

This is the part of the design that took the most iteration, so it's worth recording *why*, not just *what*:

1. **DSFTool's text format supports `#` comments natively** (confirmed empirically - its own output uses them, e.g. `# Result code: 0`). Prefixing an `OBJECT`/`POLYGON`/`NETWORK` line with a marker comment (`text_model.DISABLED_MARKER`) and recompiling produces a binary with that instance completely absent - verified via round-trip testing (comment out → compile → decompile → 0 instances remain, restore → recompile → same instances back, modulo DSF's own coordinate-quantization rounding).
2. **The binary DSF format has no "disabled but present" state** - once compiled, an object is either there or it isn't. So "disabling" a type means: decompile → drop matching instance lines/blocks → recompile → overwrite the live tile. `POLYGON`/`NETWORK` entries are multi-line blocks (`BEGIN_POLYGON...END_POLYGON`, `BEGIN_SEGMENT...END_SEGMENT`) and must be dropped as whole blocks; `OBJECT` entries are single lines. Definition tables (`OBJECT_DEF`/`POLYGON_DEF`/`NETWORK_DEF`) are always preserved in full, even at zero active instances, so indices stay stable. All of this lives in `text_model.py` (`parse`/`render`/`kind_of_line`).
3. **We rejected duplicating whole scenery packs (an "overlay" approach)** in favor of mutating tiles in place - simHeaven tiles are large and the disabled content is expected to be the minority, so duplicating the majority (kept) content to avoid touching the original was the wrong trade.
4. **We also rejected a full pristine-copy backup per touched tile** (an earlier `.xwcorig` design, kept here for history but superseded). The install this app manages is 100+ GB and only grows, and it turned out unnecessary: `apply.py`'s write path always decompiles the **live tile** first, which by construction already contains everything currently *enabled* - there is nothing to restore for that half. The only data that's genuinely unrecoverable once filtered out is the *disabled* half, so that's the only thing worth persisting. `backup.py` stores exactly that in a **`.xwcdisabled` sidecar** next to the tile: gzip-compressed text, one `#TYPE <name>` marker per group followed by each removed instance's exact raw source lines (reusing `text_model`'s block-scanning so a multi-line `POLYGON`/`NETWORK` instance is never split incorrectly). Untouched tiles get no sidecar at all. This is the same "kept + removed = original" insight as the rejected `.xwcorig` design, just realized in its leanest form - don't ever persist the "kept" half, since it's already sitting right there in the live file.
5. **The durable, persisted "configuration" is a type-*name* list per folder** (`Settings.disabled_types` in `config.py`), not literal per-instance data. This is what survives simHeaven updates: a fresh download naturally restores full pristine content, and "reapplying" your prior choices just means re-running the same type-name filter against the new file.
6. **Drift detection is manual, not automatic, and hard-blocking when it fires** (`main_window._resolve_drift`, run once at startup before the tree is populated). `backup.HashStore` remembers the hash this app expects each touched tile to have; if a tile's disabled-type folder has drifted, `gui/dialogs.DriftChoiceDialog` forces a choice between reapplying the existing configuration (`apply_folders`) or resetting to fully enabled (`reset_folders`, which only ever deletes sidecar bookkeeping - the drift itself already means the live file has fresh, complete content, so there's nothing to restore). Folders whose directory no longer exists at all have their config entries garbage-collected outright rather than left as dead weight.
7. **Every DSF write follows one invariant to prevent duplicate instances**: `apply._apply_tile` always decompiles the live tile fresh, merges in whatever the sidecar currently holds, and re-renders the *complete* result with the current disabled-type set commented out - never incrementally splicing fragments into an already-modified live file. A type's instances only ever come from one place (live text or sidecar) on any given write, so re-toggling can never double-count.
8. **Bulk scope for disabling a type**: region right-click (all categories in that region), category right-click (that category across every region, same edition), type/type-group right-click (that type or class of types across every region, same category + edition) - all implemented in `main_window.py`'s context-menu handlers, all funneled through the single `apply_folders`/`_run_apply_with_progress` write path.

### Implementation details worth knowing before touching this code

- **Reentrancy guard (`_write_in_progress`)**: showing a modal `ProgressDialog`'s nested `exec()` loop from directly inside a `QTreeWidget.itemChanged` handler was observed to cause Qt to redeliver the same `itemChanged` event before the first call returned, recursing without end. `_run_apply_with_progress`/`_run_reset_with_progress` are the single choke point for every write (single toggle, cross-region bulk, drift resolution), so guarding there covers all of them and also guarantees only one file-mutating operation is ever in flight.
- **`paths.app_root()` is `@lru_cache`d.** Repeatedly resolving it under heavy concurrency (every DSFTool invocation from every worker thread calls it via `dsftool_path()`) was observed to race CPython's internal realpath symlink-resolution cache and raise a spurious `RecursionError`.
- **Worker counts leave headroom** (`concurrency.default_worker_count()`): 2 cores free on machines with ≤8 cores, 4 free on larger ones, never fewer than 1 worker. Shared by both `inventory.py`'s scanning and `apply.py`'s write path.
- **Undismissable dialogs by design** (`gui/dialogs._UndismissableDialog`): no close button, Escape ignored, closable only by the caller once the background work actually finishes. There's nothing safe to let the user interrupt mid-write, and an unanswered drift choice would itself be exactly the "UI and files disagree" state this whole mechanism exists to avoid.
- **A type's total instance count within one folder never changes from toggling it** - disabling just moves instances between the live tile and the sidecar. `_toggled_type_count()` in `main_window.py` computes the new active/disabled split directly from the old total, so the tree updates immediately without waiting for a rescan.

## Architecture

```
main.py                          entry point; applies theme, shows MainWindow
xworldconfig/
  paths.py                       portable path resolution: everything (config, cache, hash store, utilities/) lives next to the executable, not an OS app-data dir. app_root() is lru_cache'd (see above).
  config.py                      Settings dataclass (custom_scenery_dir, freeware/pro prefixes, disabled_types: dict[folder_name, list[type_name]]) <-> xworldconfig.json next to the exe
  ini_parser.py                  SceneryPacksIni: read/toggle/write scenery_packs.ini, preserving order & every untouched line
  scenery/
    discovery.py                 finds simHeaven folders, parses region/sequence/category/variant generically from folder names (no hardcoded category vocabulary), correlates each against the ini
  dsf/
    dsftool.py                   subprocess wrapper around the per-OS DSFTool binary (--dsf2text / --text2dsf)
    concurrency.py                default_worker_count(): shared worker-count sizing for inventory.py and apply.py, leaves 2-4 cores free
    text_model.py                 parse()/render(): DSF text intermediate <-> structured defs+instances, comment-marker filtering, shared block-scanning (kind_of_line, _consume_instance) reused by backup.py's sidecar format
    backup.py                     the .xwcdisabled sidecar (gzip text, removed-instance records grouped by type) + HashStore (drift detection, persisted expected-hash per tile)
    apply.py                      the canonical write path: apply_folder(s) disables/enables types (decompile live -> merge sidecar -> render -> recompile -> rewrite sidecar+hash); reset_folder(s) discards sidecar records for the drift "reset to enabled" path without touching live tiles
    inventory.py                  scan_folder()/scan_folders(): decompile tiles, count instances per type (active from the live tile, disabled from the sidecar), cached, concurrent, cross-folder flattened for "Scan All Objects"
    scan_cache.py                 persists per-tile counts keyed by (path, size, mtime) next to the exe - pure speed cache, safe to delete
  gui/
    main_window.py                 the whole UI: edition -> region -> category -> object-type tree, category checkboxes (ini) and type checkboxes (apply.py) both live, lazy per-category scan, bulk "Scan All Objects", region/category/type/type-group right-click bulk actions, startup drift resolution, single-write-path reentrancy guard
    dialogs.py                     ProgressDialog (undismissable, pure feedback) and DriftChoiceDialog (undismissable, forces apply-vs-reset) - every DSF write goes through one of these
    formatting.py                  render_progress_bar(): shared text-based progress bar used by both the inline scan progress label and ProgressDialog
    theme.py                       Fusion style + palette-aware stylesheet (visible button borders/hover in both light and dark) + app-wide font bump
```

## Packaging plan (not yet done)

PyInstaller `--onedir` (not `--onefile` - avoids self-extract-on-every-launch), shipped as a folder with the executable, `utilities/` (per-OS DSFTool binaries), and the settings/cache JSON files all as siblings - fully portable, no installer, no OS app-data directory involved. On macOS this still means a `.app` bundle (idiomatic "portable" there), with config/utilities kept next to the bundle rather than inside `Contents/`.

## Scale characteristics (from real testing against the user's actual install)

- ~118 folders discovered across both editions on a full simHeaven Pro + freeware install; up to ~130 category folders total.
- A single footprints tile can be 35 MB binary / 235 MB decompiled text; one region's footprints folder had 155.7 million total object instances.
- Cold full-folder scan (with concurrency): dropped from 115s (serial) to 44s for one heavy folder. Warm (cached) rescan of the same folder: 0.07s.
- The `.xwcdisabled` sidecar's gzip-compressed, keyword-prefixed text format compresses to roughly a quarter of its uncompressed size.
- This is why scanning is lazy per-category by default, cached to disk keyed by (size, mtime), why "Scan All Objects" flattens every folder's tiles into one shared thread pool rather than a pool per folder, and why worker counts deliberately leave CPU headroom rather than saturating every core.

## Testing approach used throughout

Every module was validated against the user's *real* X-Plane install (read-only where possible) rather than only synthetic data, plus isolated fixture directories (copied `scenery_packs.ini` + empty folders in the OS temp/scratch dir) whenever a test needed to *write* something, so the real install is never touched by test runs. Examples worth reusing as a pattern: the DSFTool comment/round-trip test, the freeware-only vs. pro-only ini-blocking scenarios, the region-context-menu bulk toggle test with a mix of enabled/disabled/unregistered folders in one fixture.

## Git / environment notes

- Remote: `github.com/wmayr88/xworldconfig`, `main` branch, pushed after every meaningful change (only commit/push when the user asks, per standing Claude Code convention, but this project's cadence so far has been "commit after each validated change").
- Local dev: `.venv` (gitignored), `pyproject.toml` declares `PySide6` + `pyinstaller` (dev extra).
- `xworldconfig.json` (settings), `xworldconfig_cache.json` (scan cache), and `xworldconfig_hashes.json` (drift hash store) all live at the repo root during dev (mirroring "next to the executable") and are gitignored - never commit them, and don't assume their contents reflect a fresh install (they currently hold the developer's real Custom Scenery path and live disabled-type state from testing).

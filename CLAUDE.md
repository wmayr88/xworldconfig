# xworldconfig

A portable Python/PySide6 GUI for managing simHeaven X-World scenery in X-Plane 12: enabling/disabling whole scenery-pack folders via `scenery_packs.ini`, and (in progress) enabling/disabling individual object/polygon/network types within a folder's DSF tiles.

## What it does today

- Discovers simHeaven scenery folders under a user-chosen `Custom Scenery` directory, for both the freeware (`simHeaven_X-World_`) and Pro (`simHeaven_X-WORLD-Pro_`) editions (prefixes are configurable in the UI, in case someone renamed folders).
- Correlates each folder against `scenery_packs.ini` and lets the user toggle whole-folder enable/disable (writes `SCENERY_PACK`/`SCENERY_PACK_DISABLED` directly).
- Lazily decompiles a folder's DSF tiles (via DSFTool) to show a per-object-type inventory with counts, cached on disk so repeat scans are near-instant.
- A "Scan All Objects" button does this across every discovered folder at once, in parallel, with a progress bar.
- Does **not** yet let you actually disable an individual object type — that's the next major piece (see "Not yet built" below).

## Non-negotiable ground rules (from the user)

- **Never reorder, add, or remove lines in `scenery_packs.ini`** beyond flipping `SCENERY_PACK` ↔ `SCENERY_PACK_DISABLED` for an *existing* line. Load-order management is explicitly out of scope — other tools already do that. If a folder exists on disk but has no line in the ini yet, the app must not add one; it must tell the user to launch X-Plane once (which writes it) and rescan. See `ini_parser.py` / `discovery.py`.
- **Freeware and Pro are fully separate configuration domains.** Never let a bulk action or scope selection cross between them, even if both are installed simultaneously (the Pro version is meant to replace freeware, but both-installed is a real scenario the app must handle correctly - verified with an isolated-freeware-only and isolated-pro-only test fixture).
- **Never take the destructive path when a non-destructive one exists.** The whole design of the not-yet-built disable mechanism (below) exists specifically to avoid ever losing data irreversibly.

## The disable mechanism (designed, not yet implemented)

This is the part of the design that took the most iteration, so it's worth recording *why*, not just *what*:

1. **DSFTool's text format supports `#` comments natively** (confirmed empirically - its own output uses them, e.g. `# Result code: 0`). Prefixing an `OBJECT`/`POLYGON`/`NETWORK` line with a marker comment and recompiling produces a binary with that instance completely absent - verified via round-trip testing (comment out → compile → decompile → 0 instances remain, restore → recompile → same instances back, modulo DSF's own coordinate-quantization rounding).
2. **The binary DSF format has no "disabled but present" state** - once compiled, an object is either there or it isn't. So "disabling" a type means: decompile → drop matching instance lines/blocks → recompile → overwrite the live tile. `POLYGON`/`NETWORK` entries are multi-line blocks (`BEGIN_POLYGON...END_POLYGON`, `BEGIN_SEGMENT...END_SEGMENT`) and must be dropped as whole blocks; `OBJECT` entries are single lines. Definition tables (`OBJECT_DEF`/`POLYGON_DEF`/`NETWORK_DEF`) are always preserved in full, even at zero active instances, so indices stay stable.
3. **We rejected duplicating whole scenery packs (an "overlay" approach)** in favor of mutating tiles in place, because simHeaven tiles are large (a single footprints tile can be tens of MB binary / hundreds of MB decompiled) and the user was clear the disabled content is expected to be the *minority* - duplicating the majority (kept) content to avoid touching the original was the wrong trade.
4. **Given in-place mutation, reversibility requires a backup - but only a *lazy, per-tile* one**: the first time a tile is actually touched, its pristine original `.dsf` binary is copied to a sibling file (`<tile>.dsf.xwcorig` - see `xworldconfig/dsf/backup.py`, `BACKUP_SUFFIX`). Binary, not decompiled text: text is ~5.4x larger. This sibling lives right next to the live tile (not in app-data), confirmed safe because X-Plane resolves tiles by exact expected filename per lat/lon band, not by directory-scanning for `*.dsf` - a stray `.xwcorig` file won't be picked up. (Still want one real in-sim smoke test before shipping.) Untouched tiles get zero backup, matching the "disabled is the minority" expectation.
5. **The durable, persisted "configuration" is a type-*name* list per folder, not literal per-instance data.** This is the key insight that makes the design survive simHeaven updates: if the user re-downloads/updates a scenery pack, the update naturally restores full pristine content (the app never touched *that* copy), and "reapplying" your prior choices just means re-running the same type-name filter against the new file - no backup of literal old data is needed for this case. The backup is *only* needed for the case of toggling a type back on within the same file version (that exact placement data is truly gone from the live file once filtered, and can't be regenerated - it has to come from somewhere).
6. **Drift detection is manual, not automatic, and hard-blocking when it fires.** If the live tile's hash no longer matches what the app last wrote, that's a sign of an external change (almost certainly a simHeaven update). The app must show a popup with a diff summary (counts per type) and let the user choose "apply existing configuration" or "start a new configuration" - never silently reapply. (This mirrors the same hard-block pattern already implemented for `scenery_packs.ini` gaps in `main_window.py` - popup + disabled tree until resolved, not just a status-bar note.)
7. **Every DSF write must follow one invariant to prevent duplicate instances**: always regenerate the compiled output from the canonical backup + the *current* full disabled-type set, and overwrite the live file - never incrementally splice fragments into an already-modified live file. This is what makes toggling, reapplying after an update, and starting a new configuration all safe by construction, with no special-casing needed to avoid double-counting.
8. **Bulk scope for disabling a type**: the user can apply to just the current folder, or to "all folders of the same category across every region" (e.g. disabling `bench` in one `*-details` folder also offers to disable it in every other continent's `*-details` folder) - never silently crossing editions.

None of `text_model.py`, the rest of `backup.py` (`create_backup`/`hash_file`/`detect_drift`), or `apply.py` are implemented yet - they're stubbed with signatures/docstrings reflecting the design above. This is intentionally the next thing to build, and should be tested in isolation (like `discovery.py`/`inventory.py` were, against real tiles with round-trip verification) before wiring into the GUI.

## Architecture

```
main.py                          entry point; applies theme, shows MainWindow
xworldconfig/
  paths.py                       portable path resolution: everything (config, cache, utilities/) lives next to the executable, not an OS app-data dir - resolved via sys.frozen when packaged, else the repo root
  config.py                      Settings dataclass (custom_scenery_dir, freeware/pro prefixes, disabled_types) <-> xworldconfig.json next to the exe
  ini_parser.py                  SceneryPacksIni: read/toggle/write scenery_packs.ini, preserving order & every untouched line
  scenery/
    discovery.py                 finds simHeaven folders, parses region/sequence/category/variant generically from folder names (no hardcoded category vocabulary - freeware and Pro have different category sets), correlates each against the ini
  dsf/
    dsftool.py                   subprocess wrapper around the per-OS DSFTool binary (--dsf2text / --text2dsf)
    inventory.py                 scan_folder()/scan_folders(): decompile tiles, count instances per type, active/disabled split (disabled always 0 until apply.py exists), cached, concurrent, cross-folder flattened for "Scan All Objects"
    scan_cache.py                persists per-tile counts keyed by (path, size, mtime) next to the exe - pure speed cache, safe to delete
    text_model.py                STUB - parse/render DSF text with comment-marker filtering (needed by apply.py)
    backup.py                    PARTIAL STUB - backup_path()/has_backup() implemented; create_backup()/hash_file()/detect_drift() not yet
    apply.py                     STUB - the canonical backup -> filter -> compile -> overwrite write path
  gui/
    main_window.py                the whole UI: edition -> region -> category -> object-type tree, checkboxes wired to the ini, lazy per-category scan, bulk "Scan All Objects" with progress bar, region right-click Enable All/Disable All
    theme.py                      Fusion style + palette-aware stylesheet (visible button borders/hover in both light and dark) + app-wide font bump
```

## Packaging plan (not yet done)

PyInstaller `--onedir` (not `--onefile` - avoids self-extract-on-every-launch), shipped as a folder with the executable, `utilities/` (per-OS DSFTool binaries), and the settings/cache JSON files all as siblings - fully portable, no installer, no OS app-data directory involved. On macOS this still means a `.app` bundle (idiomatic "portable" there), with config/utilities kept next to the bundle rather than inside `Contents/`.

`utilities/NOTICE.md` carries the third-party attribution for the bundled DSFTool binaries (MIT/X11, Laminar Research's X-Plane Scenery Tools project) - keep it alongside `utilities/` in any packaged build, since MIT requires the copyright/permission notice to travel with redistributed copies.

## Scale characteristics (from real testing against the user's actual install)

- ~118 folders discovered across both editions on a full simHeaven Pro + freeware install; up to ~130 category folders total.
- A single footprints tile can be 35 MB binary / 235 MB decompiled text; one region's footprints folder had 155.7 million total object instances.
- Cold full-folder scan (with concurrency): dropped from 115s (serial) to 44s for one heavy folder. Warm (cached) rescan of the same folder: 0.07s.
- This is why scanning is lazy per-category by default, cached to disk keyed by (size, mtime), and why "Scan All Objects" flattens every folder's tiles into one shared thread pool rather than a pool per folder.

## Testing approach used throughout

Every module was validated against the user's *real* X-Plane install (read-only where possible) rather than only synthetic data, plus isolated fixture directories (copied `scenery_packs.ini` + empty folders in the OS temp/scratch dir) whenever a test needed to *write* something, so the real install is never touched by test runs. Examples worth reusing as a pattern: the DSFTool comment/round-trip test, the freeware-only vs. pro-only ini-blocking scenarios, the region-context-menu bulk toggle test with a mix of enabled/disabled/unregistered folders in one fixture.

## Git / environment notes

- Remote: `github.com/wmayr88/xworldconfig`, `main` branch, pushed after every meaningful change (only commit/push when the user asks, per standing Claude Code convention, but this project's cadence so far has been "commit after each validated change").
- Local dev: `.venv` (gitignored), `pyproject.toml` declares `PySide6` + `pyinstaller` (dev extra).
- `xworldconfig.json` (settings) and `xworldconfig_cache.json` (scan cache) both live at the repo root during dev (mirroring "next to the executable") and are gitignored - never commit them, and don't assume their contents reflect a fresh install (they currently hold the developer's real Custom Scenery path from testing).

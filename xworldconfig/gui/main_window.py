"""Main application window: an edition/region/category/object-type tree built
from xworldconfig.scenery.discovery and lazily populated per-category from
xworldconfig.dsf.inventory. Whole-folder enable/disable is wired to
scenery_packs.ini via ini_parser. Per-type enable/disable is wired to
xworldconfig.dsf.apply: an item checkbox toggles that one type in that one
folder; item/class right-click bulk-toggles across every folder of the same
category and edition. Every write runs behind a modal ProgressDialog (see
xworldconfig.gui.dialogs) since it's mutating real DSF files. A startup
drift check (_resolve_drift) catches folders whose disabled-type
configuration no longer matches what's on disk (almost always a simHeaven
update) before the tree is ever shown, resolved via a single combined
DriftChoiceDialog - never left in a state where the UI and the files
disagree."""
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from xworldconfig import config
from xworldconfig.dsf.apply import ApplyResult, apply_folders, reset_folders
from xworldconfig.dsf.backup import HashStore, has_disabled_records
from xworldconfig.dsf.inventory import ScanResult, TypeCount, scan_folder, scan_folders, tile_breakdown
from xworldconfig.gui.dialogs import DriftChoiceDialog, ProgressDialog, TileBreakdownDialog
from xworldconfig.gui.formatting import render_progress_bar
from xworldconfig.ini_parser import SceneryPacksIni
from xworldconfig.scenery.discovery import SceneryFolder, discover

_ROLE_KIND = Qt.UserRole
_ROLE_FOLDER = Qt.UserRole + 1
_ROLE_TYPE_NAME = Qt.UserRole + 2
_ROLE_TYPE_COUNT = Qt.UserRole + 3

_EDITION_LABELS = {"freeware": "Freeware (simHeaven X-World)", "pro": "Pro (simHeaven X-World-Pro)"}
_WARNING_COLOR = QColor("#c47a1f")
_DISABLED_TYPE_COLOR = QColor("#c0392b")


class _ScanSignals(QObject):
    progress = Signal(object, int, int)  # QTreeWidgetItem, completed, total
    finished = Signal(object, object)  # QTreeWidgetItem, ScanResult
    failed = Signal(object, str)  # QTreeWidgetItem, error message


class _ScanTask(QRunnable):
    def __init__(self, item: QTreeWidgetItem, folder_path: Path, signals: _ScanSignals):
        super().__init__()
        self._item = item
        self._folder_path = folder_path
        self._signals = signals

    def run(self) -> None:
        try:
            result = scan_folder(
                self._folder_path,
                on_progress=lambda done, total: self._signals.progress.emit(self._item, done, total),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            self._signals.failed.emit(self._item, str(exc))
            return
        self._signals.finished.emit(self._item, result)


class _BulkScanSignals(QObject):
    progress = Signal(int, int)  # completed, total
    finished = Signal(object)  # dict[Path, ScanResult] - object, not dict: keys are Path, not str
    failed = Signal(str)


class _BulkScanTask(QRunnable):
    def __init__(self, folder_paths: list[Path], signals: _BulkScanSignals):
        super().__init__()
        self._folder_paths = folder_paths
        self._signals = signals

    def run(self) -> None:
        try:
            results = scan_folders(
                self._folder_paths,
                on_progress=lambda done, total: self._signals.progress.emit(done, total),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            self._signals.failed.emit(str(exc))
            return
        self._signals.finished.emit(results)


class _ApplySignals(QObject):
    progress = Signal(int, int)  # completed, total
    finished = Signal(object)  # dict[Path, ApplyResult]
    failed = Signal(str)


class _ApplyTask(QRunnable):
    def __init__(self, folder_configs: list[tuple[Path, set[str]]], signals: _ApplySignals):
        super().__init__()
        self._folder_configs = folder_configs
        self._signals = signals

    def run(self) -> None:
        try:
            results = apply_folders(
                self._folder_configs,
                on_progress=lambda done, total: self._signals.progress.emit(done, total),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            self._signals.failed.emit(str(exc))
            return
        self._signals.finished.emit(results)


class _ResetSignals(QObject):
    progress = Signal(int, int)
    finished = Signal(object)  # dict[Path, int]
    failed = Signal(str)


class _ResetTask(QRunnable):
    def __init__(self, folder_paths: list[Path], signals: _ResetSignals):
        super().__init__()
        self._folder_paths = folder_paths
        self._signals = signals

    def run(self) -> None:
        try:
            results = reset_folders(
                self._folder_paths,
                on_progress=lambda done, total: self._signals.progress.emit(done, total),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            self._signals.failed.emit(str(exc))
            return
        self._signals.finished.emit(results)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("xworldconfig")
        self.resize(1100, 750)

        self.settings = config.load()
        self._thread_pool = QThreadPool.globalInstance()
        self._populating = False
        self._active_scans: list[_ScanSignals] = []
        self._active_bulk_scans: list[_BulkScanSignals] = []
        self._active_apply_signals: list[_ApplySignals] = []
        self._active_reset_signals: list[_ResetSignals] = []
        self._write_in_progress = False
        self._bulk_scan_running = False
        self._ini_blocked = False
        self._known_folders: list[SceneryFolder] = []
        self._category_items: dict[Path, QTreeWidgetItem] = {}

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Count"])
        self.tree.setColumnWidth(0, 480)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        settings_form = QFormLayout()
        settings_form.setContentsMargins(0, 0, 0, 0)
        self.folder_path_edit = QLineEdit()
        self.folder_path_edit.setReadOnly(True)
        settings_form.addRow("Custom Scenery Folder:", self.folder_path_edit)
        self.freeware_prefix_edit = QLineEdit(self.settings.freeware_prefix)
        self.freeware_prefix_edit.editingFinished.connect(self._on_prefix_changed)
        settings_form.addRow("X-World folder prefix:", self.freeware_prefix_edit)
        self.pro_prefix_edit = QLineEdit(self.settings.pro_prefix)
        self.pro_prefix_edit.editingFinished.connect(self._on_prefix_changed)
        settings_form.addRow("X-World Pro folder prefix:", self.pro_prefix_edit)
        layout.addLayout(settings_form)

        button_row = QHBoxLayout()
        self.choose_button = QPushButton("Custom Scenery Folder")
        self.choose_button.clicked.connect(self._choose_scenery_folder)
        button_row.addWidget(self.choose_button)
        self.scan_button = QPushButton("Scan Folder")
        self.scan_button.clicked.connect(self._rescan)
        button_row.addWidget(self.scan_button)
        self.scan_all_button = QPushButton("Scan All Objects")
        self.scan_all_button.clicked.connect(self._on_scan_all_clicked)
        button_row.addWidget(self.scan_all_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.progress_label = QLabel()
        self.progress_label.setStyleSheet("font-family: monospace;")
        self.progress_label.setVisible(False)
        layout.addWidget(self.progress_label)

        layout.addWidget(self.tree, 1)
        self.setCentralWidget(central)

        self._update_folder_path_field()
        if self.settings.custom_scenery_dir and Path(self.settings.custom_scenery_dir).is_dir():
            self._resolve_drift()
            self._rescan()
        else:
            self._choose_scenery_folder()

    def _update_folder_path_field(self) -> None:
        self.folder_path_edit.setText(self.settings.custom_scenery_dir or "(no folder selected)")

    def _choose_scenery_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose Custom Scenery folder")
        if not chosen:
            return
        self.settings.custom_scenery_dir = chosen
        config.save(self.settings)
        self._update_folder_path_field()
        self._rescan()

    def _on_prefix_changed(self) -> None:
        freeware = self.freeware_prefix_edit.text().strip()
        pro = self.pro_prefix_edit.text().strip()
        if freeware == self.settings.freeware_prefix and pro == self.settings.pro_prefix:
            return
        self.settings.freeware_prefix = freeware
        self.settings.pro_prefix = pro
        config.save(self.settings)
        self._rescan()

    def _resolve_drift(self) -> None:
        """Runs once at startup, before the tree is populated: catches the
        case where a folder with disabled-type config has changed on disk
        since this app last wrote it (almost always a simHeaven update),
        before the user ever looks at that folder. Cheap - only folders
        with disabled_types entries are checked, only their tiles that
        already have a .xwcdisabled sidecar are hashed, no decompiling.

        Folders that don't exist at all (deleted, not just updated) carry no
        useful config - those entries are dropped outright rather than left
        as dead weight, so a later reinstall under the same name is treated
        as brand new (fresh scan, fresh metadata) instead of surfacing a
        stale disabled-type list with nothing left to compare it against."""
        if not self.settings.disabled_types or not self.settings.custom_scenery_dir:
            return
        scenery_dir = Path(self.settings.custom_scenery_dir)

        missing = [name for name in self.settings.disabled_types if not (scenery_dir / name).is_dir()]
        if missing:
            for name in missing:
                self.settings.disabled_types.pop(name, None)
            config.save(self.settings)

        if not self.settings.disabled_types:
            return
        hash_store = HashStore()

        drifted: list[tuple[str, Path]] = []
        for folder_name, type_names in self.settings.disabled_types.items():
            if not type_names:
                continue
            folder_path = scenery_dir / folder_name
            nav_data = folder_path / "Earth nav data"
            if not nav_data.is_dir():
                continue
            touched_tiles = [t for t in nav_data.glob("**/*.dsf") if has_disabled_records(t)]
            if any(hash_store.detect_drift(t) for t in touched_tiles):
                drifted.append((folder_name, folder_path))

        if not drifted:
            return

        dialog = DriftChoiceDialog(self, [name for name, _ in drifted])
        choice = dialog.exec_and_get_choice()

        if choice == "apply":
            folder_configs = [(path, set(self.settings.disabled_types[name])) for name, path in drifted]
            self._run_apply_with_progress(
                folder_configs,
                f"Reapplying configuration to {len(drifted)} updated folder(s)...",
                on_success=lambda results: None,
            )
        else:
            for name, _ in drifted:
                self.settings.disabled_types.pop(name, None)
            config.save(self.settings)
            self._run_reset_with_progress(
                [path for _, path in drifted],
                f"Resetting {len(drifted)} updated folder(s) to enabled...",
            )

    def _run_apply_with_progress(
        self,
        folder_configs: list[tuple[Path, set[str]]],
        description: str,
        on_success: Callable[[dict[Path, ApplyResult]], None],
        on_failure: Callable[[], None] | None = None,
    ) -> None:
        # Reentrancy guard: showing a modal ProgressDialog's nested exec()
        # loop from directly inside an itemChanged handler was observed to
        # cause Qt to redeliver the same itemChanged event before the first
        # call returns, recursing without end. This is the single choke
        # point for every write path (single toggle, cross-region bulk,
        # drift resolution), so guarding here covers all of them and also
        # guarantees only one file-mutating operation is ever in flight.
        if self._write_in_progress:
            return
        self._write_in_progress = True
        try:
            dialog = ProgressDialog(self, "Applying Changes", description)
            signals = _ApplySignals()
            self._active_apply_signals.append(signals)  # keep alive until the queued callback fires

            def handle_finished(results: dict) -> None:
                dialog.force_close()
                on_success(results)

            def handle_failed(message: str) -> None:
                dialog.force_close()
                QMessageBox.warning(self, "Apply failed", message)
                if on_failure is not None:
                    on_failure()

            signals.progress.connect(dialog.set_progress)
            signals.finished.connect(handle_finished)
            signals.failed.connect(handle_failed)
            self._thread_pool.start(_ApplyTask(folder_configs, signals))
            dialog.exec()
        finally:
            self._write_in_progress = False

    def _run_reset_with_progress(self, folder_paths: list[Path], description: str) -> None:
        if self._write_in_progress:
            return
        self._write_in_progress = True
        try:
            dialog = ProgressDialog(self, "Resetting Folders", description)
            signals = _ResetSignals()
            self._active_reset_signals.append(signals)  # keep alive until the queued callback fires

            def handle_finished(results: dict) -> None:
                dialog.force_close()

            def handle_failed(message: str) -> None:
                dialog.force_close()
                QMessageBox.warning(self, "Reset failed", message)

            signals.progress.connect(dialog.set_progress)
            signals.finished.connect(handle_finished)
            signals.failed.connect(handle_failed)
            self._thread_pool.start(_ResetTask(folder_paths, signals))
            dialog.exec()
        finally:
            self._write_in_progress = False

    def _rescan(self) -> None:
        self.tree.clear()
        self.tree.setEnabled(True)
        if not self.settings.custom_scenery_dir:
            return
        scenery_dir = Path(self.settings.custom_scenery_dir)

        ini_path = scenery_dir / "scenery_packs.ini"
        ini_missing = not ini_path.exists()
        ini = None if ini_missing else SceneryPacksIni(ini_path)
        if ini_missing:
            QMessageBox.warning(
                self,
                "scenery_packs.ini not found",
                f"No scenery_packs.ini was found at:\n{ini_path}\n\n"
                "Launch X-Plane at least once so it can generate this file, then click Scan Folder again.",
            )

        folders = discover(scenery_dir, self.settings.freeware_prefix, self.settings.pro_prefix, ini)
        self._populate_tree(folders, ini_missing)

    def _populate_tree(self, folders: list[SceneryFolder], ini_missing: bool) -> None:
        self._populating = True
        try:
            self._known_folders = folders
            self._category_items = {}
            missing_count = sum(1 for f in folders if f.ini_entry is None)

            by_edition: dict[str, list[SceneryFolder]] = {}
            for folder in folders:
                by_edition.setdefault(folder.edition, []).append(folder)

            for edition in ("freeware", "pro"):
                edition_folders = by_edition.get(edition)
                if not edition_folders:
                    continue
                edition_item = QTreeWidgetItem([_EDITION_LABELS[edition], ""])
                edition_item.setData(0, _ROLE_KIND, "edition")
                self.tree.addTopLevelItem(edition_item)

                by_region: dict[str | None, list[SceneryFolder]] = {}
                for folder in edition_folders:
                    by_region.setdefault(folder.region, []).append(folder)

                for region in sorted(by_region, key=lambda r: (r is None, r or "")):
                    region_label = region if region is not None else "(Other / Library)"
                    region_item = QTreeWidgetItem([region_label, ""])
                    region_item.setData(0, _ROLE_KIND, "region")
                    edition_item.addChild(region_item)

                    region_folders = sorted(
                        by_region[region], key=lambda f: (f.sequence if f.sequence is not None else 0, f.path.name)
                    )
                    for folder in region_folders:
                        region_item.addChild(self._build_category_item(folder))

                    region_item.setExpanded(True)

                edition_item.setExpanded(True)

            self._ini_blocked = bool(missing_count)
            self.scan_all_button.setEnabled(not self._ini_blocked)
            if missing_count:
                self.tree.setEnabled(False)
                self.statusBar().showMessage(
                    f"Blocked: {missing_count} folder(s) not yet in scenery_packs.ini. "
                    "Launch X-Plane, then click Scan Folder again.",
                )
                if not ini_missing:
                    QMessageBox.warning(
                        self,
                        "Folders not registered in scenery_packs.ini",
                        f"{missing_count} simHeaven folder(s) were found on disk but are not yet "
                        "listed in scenery_packs.ini.\n\n"
                        "X-Plane adds new entries to this file on launch, so it likely hasn't been "
                        "started since these folders were installed.\n\n"
                        "Launch X-Plane at least once, then click Scan Folder again. The scenery list "
                        "stays disabled until every discovered folder is accounted for.",
                    )
            else:
                self.tree.setEnabled(True)
                self.statusBar().showMessage(f"Scan complete: {len(folders)} folder(s) found.", 5000)
        finally:
            self._populating = False

    def _build_category_item(self, folder: SceneryFolder) -> QTreeWidgetItem:
        if folder.category:
            label = folder.category
            if folder.sequence is not None:
                label = f"{folder.sequence:02d} - {label}"
            if folder.variant:
                label = f"{label} ({folder.variant})"
        else:
            label = folder.path.name

        item = QTreeWidgetItem([label, ""])
        item.setData(0, _ROLE_KIND, "category")
        item.setData(0, _ROLE_FOLDER, folder)
        self._category_items[folder.path] = item

        if folder.ini_entry is None:
            item.setText(1, "not in scenery_packs.ini")
            item.setForeground(1, QBrush(_WARNING_COLOR))
            item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
        else:
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if folder.ini_entry.enabled else Qt.Unchecked)

        placeholder = QTreeWidgetItem(["(scan not yet loaded - expand to scan)", ""])
        placeholder.setData(0, _ROLE_KIND, "placeholder")
        item.addChild(placeholder)
        return item

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _ROLE_KIND) != "category":
            return
        folder: SceneryFolder = item.data(0, _ROLE_FOLDER)
        self._start_lazy_scan(item, folder)

    def _start_lazy_scan(self, item: QTreeWidgetItem, folder: SceneryFolder) -> bool:
        if not self._is_unscanned(item):
            return False
        item.child(0).setText(0, "Scanning...")

        signals = _ScanSignals()
        signals.progress.connect(self._on_lazy_scan_progress)
        signals.finished.connect(self._on_scan_finished)
        signals.failed.connect(self._on_scan_failed)
        self._active_scans.append(signals)  # keep alive until the queued callback fires
        self._thread_pool.start(_ScanTask(item, folder.path, signals))
        return True

    def _on_lazy_scan_progress(self, item: QTreeWidgetItem, done: int, total: int) -> None:
        if item.childCount() != 1 or item.child(0).data(0, _ROLE_KIND) != "placeholder":
            return  # already finished/replaced by the time this queued signal arrived
        placeholder = item.child(0)
        if total <= 0:
            placeholder.setText(0, "Scanning...")
        else:
            placeholder.setText(0, f"Scanning... ({done:,} / {total:,} tiles)")

    def _is_unscanned(self, category_item: QTreeWidgetItem) -> bool:
        if category_item.childCount() != 1:
            return False
        placeholder = category_item.child(0)
        return placeholder.data(0, _ROLE_KIND) == "placeholder" and placeholder.text(0) != "Scanning..."

    def _on_scan_finished(self, item: QTreeWidgetItem, result: ScanResult) -> None:
        folder: SceneryFolder = item.data(0, _ROLE_FOLDER)
        item.takeChildren()
        if not result.counts:
            empty = QTreeWidgetItem(["(no objects/polygons/networks found)", ""])
            empty.setData(0, _ROLE_KIND, "info")
            item.addChild(empty)
        else:
            current_kind = None
            kind_item = None
            for type_count in result.counts:
                if type_count.kind != current_kind:
                    current_kind = type_count.kind
                    kind_item = QTreeWidgetItem([_kind_heading(current_kind), ""])
                    kind_item.setData(0, _ROLE_KIND, "type_group")
                    item.addChild(kind_item)
                row = QTreeWidgetItem([_short_type_name(type_count.type_name), ""])
                row.setData(0, _ROLE_KIND, "type")
                row.setData(0, _ROLE_FOLDER, folder)
                row.setData(0, _ROLE_TYPE_NAME, type_count.type_name)
                row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
                self._set_type_row_state(row, type_count)
                kind_item.addChild(row)
            if kind_item is not None:
                for i in range(item.childCount()):
                    item.child(i).setExpanded(True)

        if result.failed_tiles:
            warning = QTreeWidgetItem([f"{len(result.failed_tiles)} tile(s) failed to decompile", ""])
            warning.setData(0, _ROLE_KIND, "info")
            warning.setForeground(0, QBrush(_WARNING_COLOR))
            item.addChild(warning)

    def _on_scan_failed(self, item: QTreeWidgetItem, message: str) -> None:
        item.takeChildren()
        error_item = QTreeWidgetItem([f"Scan failed: {message}", ""])
        error_item.setData(0, _ROLE_KIND, "info")
        error_item.setForeground(0, QBrush(QColor("#c0392b")))
        item.addChild(error_item)

    def _on_scan_all_clicked(self) -> None:
        enabled_folders = [f for f in self._known_folders if f.ini_entry is not None and f.ini_entry.enabled]
        self._start_bulk_scan(enabled_folders)

    def _start_bulk_scan(self, folders: list[SceneryFolder]) -> None:
        if self._bulk_scan_running or not folders:
            return
        self._bulk_scan_running = True
        self.tree.setEnabled(False)
        self.choose_button.setEnabled(False)
        self.freeware_prefix_edit.setEnabled(False)
        self.pro_prefix_edit.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.scan_all_button.setEnabled(False)
        self.progress_label.setText(render_progress_bar(0, 0))
        self.progress_label.setVisible(True)

        folder_paths = [f.path for f in folders]
        signals = _BulkScanSignals()
        signals.progress.connect(self._on_bulk_scan_progress)
        signals.finished.connect(self._on_bulk_scan_finished)
        signals.failed.connect(self._on_bulk_scan_failed)
        self._active_bulk_scans.append(signals)  # keep alive until the queued callback fires
        self._thread_pool.start(_BulkScanTask(folder_paths, signals))

    def _on_bulk_scan_progress(self, done: int, total: int) -> None:
        self.progress_label.setText(render_progress_bar(done, total))

    def _on_bulk_scan_finished(self, results: dict) -> None:
        for folder_path, result in results.items():
            item = self._category_items.get(folder_path)
            if item is not None:
                self._on_scan_finished(item, result)
        self._end_bulk_scan()
        total_types = sum(len(r.counts) for r in results.values())
        self.statusBar().showMessage(
            f"Scan complete: {len(results)} folder(s), {total_types} type(s) found.", 5000
        )

    def _on_bulk_scan_failed(self, message: str) -> None:
        self._end_bulk_scan()
        QMessageBox.warning(self, "Scan failed", message)

    def _end_bulk_scan(self) -> None:
        self._bulk_scan_running = False
        self.progress_label.setVisible(False)
        self.choose_button.setEnabled(True)
        self.freeware_prefix_edit.setEnabled(True)
        self.pro_prefix_edit.setEnabled(True)
        self.scan_button.setEnabled(True)
        self.scan_all_button.setEnabled(not self._ini_blocked)
        self.tree.setEnabled(not self._ini_blocked)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._populating or column != 0:
            return
        kind = item.data(0, _ROLE_KIND)
        if kind == "category":
            self._on_category_checkbox_changed(item)
        elif kind == "type":
            self._on_type_checkbox_changed(item)

    def _on_category_checkbox_changed(self, item: QTreeWidgetItem) -> None:
        folder: SceneryFolder = item.data(0, _ROLE_FOLDER)
        if folder.ini_entry is None:
            return

        enabled = item.checkState(0) == Qt.Checked
        ini_path = Path(self.settings.custom_scenery_dir) / "scenery_packs.ini"
        ini = SceneryPacksIni(ini_path)
        entry = ini.find_by_folder_name(folder.path.name)
        if entry is None:
            return
        ini.set_enabled(entry, enabled)
        ini.save()
        folder.ini_entry.enabled = enabled

    def _on_type_checkbox_changed(self, item: QTreeWidgetItem) -> None:
        folder: SceneryFolder = item.data(0, _ROLE_FOLDER)
        type_name: str = item.data(0, _ROLE_TYPE_NAME)
        enabled = item.checkState(0) == Qt.Checked

        disabled_names = set(self.settings.disabled_types.get(folder.path.name, []))
        if enabled:
            disabled_names.discard(type_name)
        else:
            disabled_names.add(type_name)

        action = "Enabling" if enabled else "Disabling"
        short_name = _short_type_name(type_name)
        self._run_apply_with_progress(
            [(folder.path, disabled_names)],
            f"{action} '{short_name}' in {folder.path.name}...",
            on_success=lambda results: self._finish_single_type_toggle(folder, disabled_names, item, enabled),
            on_failure=lambda: self._revert_type_checkbox(item, enabled),
        )

    def _finish_single_type_toggle(
        self, folder: SceneryFolder, disabled_names: set[str], item: QTreeWidgetItem, enabled: bool
    ) -> None:
        if disabled_names:
            self.settings.disabled_types[folder.path.name] = sorted(disabled_names)
        else:
            self.settings.disabled_types.pop(folder.path.name, None)
        config.save(self.settings)

        old_count: TypeCount = item.data(0, _ROLE_TYPE_COUNT)
        self._set_type_row_state(item, _toggled_type_count(old_count, enabled))
        self.statusBar().showMessage(
            f"{'Enabled' if enabled else 'Disabled'} '{_short_type_name(item.data(0, _ROLE_TYPE_NAME))}'.", 5000
        )

    def _revert_type_checkbox(self, item: QTreeWidgetItem, attempted_enabled: bool) -> None:
        self._populating = True
        try:
            item.setCheckState(0, Qt.Unchecked if attempted_enabled else Qt.Checked)
        finally:
            self._populating = False

    def _set_type_row_state(self, row: QTreeWidgetItem, type_count: TypeCount) -> None:
        row.setData(0, _ROLE_TYPE_COUNT, type_count)
        enabled = type_count.disabled_count == 0
        self._populating = True
        try:
            row.setCheckState(0, Qt.Checked if enabled else Qt.Unchecked)
        finally:
            self._populating = False
        if enabled:
            row.setText(1, f"{type_count.active_count:,}")
            row.setData(1, Qt.ForegroundRole, None)
        else:
            row.setText(1, "(disabled)")
            row.setForeground(1, QBrush(_DISABLED_TYPE_COLOR))

    def _on_tree_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        kind = item.data(0, _ROLE_KIND)
        if kind == "region":
            self._show_region_context_menu(item, pos)
        elif kind == "category":
            self._show_category_context_menu(item, pos)
        elif kind == "type_group":
            self._show_class_context_menu(item, pos)
        elif kind == "type":
            self._show_type_context_menu(item, pos)

    def _show_region_context_menu(self, region_item: QTreeWidgetItem, pos) -> None:
        menu = QMenu(self)
        scan_action = menu.addAction("Scan Folders")
        menu.addSeparator()
        disable_action = menu.addAction("Disable All")
        enable_action = menu.addAction("Enable All")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is scan_action:
            self._scan_unscanned_in_region(region_item)
        elif chosen is disable_action:
            self._set_region_enabled(region_item, False)
        elif chosen is enable_action:
            self._set_region_enabled(region_item, True)

    def _show_category_context_menu(self, category_item: QTreeWidgetItem, pos) -> None:
        folder: SceneryFolder = category_item.data(0, _ROLE_FOLDER)
        menu = QMenu(self)
        scan_action = menu.addAction("Scan Folders")
        disable_all_action = None
        enable_all_action = None
        if folder.category:
            menu.addSeparator()
            disable_all_action = menu.addAction(f"Disable all {folder.category}")
            enable_all_action = menu.addAction(f"Enable all {folder.category}")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is scan_action:
            if not self._start_lazy_scan(category_item, folder):
                self.statusBar().showMessage(f"{category_item.text(0)} is already scanned.", 5000)
        elif chosen is disable_all_action:
            self._set_category_enabled_everywhere(folder, False)
        elif chosen is enable_all_action:
            self._set_category_enabled_everywhere(folder, True)

    def _show_type_context_menu(self, type_item: QTreeWidgetItem, pos) -> None:
        folder: SceneryFolder = type_item.data(0, _ROLE_FOLDER)
        type_name: str = type_item.data(0, _ROLE_TYPE_NAME)
        type_count: TypeCount = type_item.data(0, _ROLE_TYPE_COUNT)
        short_name = _short_type_name(type_name)
        menu = QMenu(self)
        tiles_action = menu.addAction("Show Tile Breakdown...")
        menu.addSeparator()
        disable_action = menu.addAction(f"Disable '{short_name}' in all regions ({folder.category})")
        enable_action = menu.addAction(f"Enable '{short_name}' in all regions ({folder.category})")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is tiles_action:
            self._show_tile_breakdown(folder, type_count.kind, type_name, short_name)
        elif chosen is disable_action:
            self._apply_type_names_everywhere(folder, {type_name}, False)
        elif chosen is enable_action:
            self._apply_type_names_everywhere(folder, {type_name}, True)

    def _show_tile_breakdown(self, folder: SceneryFolder, kind: str, type_name: str, short_name: str) -> None:
        tile_counts = tile_breakdown(folder.path, kind, type_name)
        TileBreakdownDialog(self, short_name, tile_counts).exec()

    def _show_class_context_menu(self, group_item: QTreeWidgetItem, pos) -> None:
        category_item = group_item.parent()
        if category_item is None:
            return
        folder: SceneryFolder = category_item.data(0, _ROLE_FOLDER)
        type_names = {
            group_item.child(i).data(0, _ROLE_TYPE_NAME)
            for i in range(group_item.childCount())
            if group_item.child(i).data(0, _ROLE_KIND) == "type"
        }
        if not type_names:
            return
        class_label = group_item.text(0)
        menu = QMenu(self)
        disable_action = menu.addAction(f"Disable all {class_label} in all regions ({folder.category})")
        enable_action = menu.addAction(f"Enable all {class_label} in all regions ({folder.category})")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is disable_action:
            self._apply_type_names_everywhere(folder, type_names, False)
        elif chosen is enable_action:
            self._apply_type_names_everywhere(folder, type_names, True)

    def _apply_type_names_everywhere(self, folder: SceneryFolder, type_names: set[str], enabled: bool) -> None:
        matching_folders = [
            f for f in self._known_folders if f.edition == folder.edition and f.category == folder.category
        ]
        folder_configs: list[tuple[Path, set[str]]] = []
        new_disabled_by_name: dict[str, set[str]] = {}
        for f in matching_folders:
            current = set(self.settings.disabled_types.get(f.path.name, []))
            if enabled:
                current -= type_names
            else:
                current |= type_names
            new_disabled_by_name[f.path.name] = current
            folder_configs.append((f.path, current))

        action = "Enabling" if enabled else "Disabling"
        self._run_apply_with_progress(
            folder_configs,
            f"{action} {len(type_names)} type(s) across {len(matching_folders)} region(s) ({folder.category})...",
            on_success=lambda results: self._finish_type_names_everywhere(
                matching_folders, new_disabled_by_name, type_names, enabled
            ),
        )

    def _finish_type_names_everywhere(
        self,
        folders: list[SceneryFolder],
        new_disabled_by_name: dict[str, set[str]],
        type_names: set[str],
        enabled: bool,
    ) -> None:
        for f in folders:
            disabled_set = new_disabled_by_name[f.path.name]
            if disabled_set:
                self.settings.disabled_types[f.path.name] = sorted(disabled_set)
            else:
                self.settings.disabled_types.pop(f.path.name, None)
        config.save(self.settings)

        for f in folders:
            item = self._category_items.get(f.path)
            if item is not None:
                self._update_type_rows_display(item, type_names, enabled)

        self.statusBar().showMessage(
            f"{'Enabled' if enabled else 'Disabled'} {len(type_names)} type(s) across {len(folders)} region(s).",
            5000,
        )

    def _update_type_rows_display(self, category_item: QTreeWidgetItem, type_names: set[str], enabled: bool) -> None:
        for i in range(category_item.childCount()):
            group_item = category_item.child(i)
            if group_item.data(0, _ROLE_KIND) != "type_group":
                continue
            for k in range(group_item.childCount()):
                row = group_item.child(k)
                if row.data(0, _ROLE_KIND) != "type" or row.data(0, _ROLE_TYPE_NAME) not in type_names:
                    continue
                old_count: TypeCount = row.data(0, _ROLE_TYPE_COUNT)
                self._set_type_row_state(row, _toggled_type_count(old_count, enabled))

    def _scan_unscanned_in_region(self, region_item: QTreeWidgetItem) -> None:
        unscanned: list[SceneryFolder] = []
        for i in range(region_item.childCount()):
            category_item = region_item.child(i)
            folder: SceneryFolder = category_item.data(0, _ROLE_FOLDER)
            if folder is None or folder.ini_entry is None or not folder.ini_entry.enabled:
                continue
            if self._is_unscanned(category_item):
                unscanned.append(folder)
        if not unscanned:
            self.statusBar().showMessage(f"Nothing to scan in {region_item.text(0)}.", 5000)
            return
        self._start_bulk_scan(unscanned)

    def _set_region_enabled(self, region_item: QTreeWidgetItem, enabled: bool) -> None:
        pairs: list[tuple[QTreeWidgetItem, SceneryFolder]] = []
        for i in range(region_item.childCount()):
            category_item = region_item.child(i)
            folder: SceneryFolder = category_item.data(0, _ROLE_FOLDER)
            if folder is not None and folder.ini_entry is not None:
                pairs.append((category_item, folder))
        self._bulk_set_enabled(pairs, enabled, region_item.text(0))

    def _set_category_enabled_everywhere(self, folder: SceneryFolder, enabled: bool) -> None:
        pairs: list[tuple[QTreeWidgetItem, SceneryFolder]] = []
        for f in self._known_folders:
            if f.edition == folder.edition and f.category == folder.category and f.ini_entry is not None:
                item = self._category_items.get(f.path)
                if item is not None:
                    pairs.append((item, f))
        description = f"'{folder.category}' ({_EDITION_LABELS[folder.edition]})"
        self._bulk_set_enabled(pairs, enabled, description)

    def _bulk_set_enabled(
        self, pairs: list[tuple[QTreeWidgetItem, SceneryFolder]], enabled: bool, description: str
    ) -> None:
        if not pairs:
            return

        ini_path = Path(self.settings.custom_scenery_dir) / "scenery_packs.ini"
        ini = SceneryPacksIni(ini_path)
        for _, folder in pairs:
            entry = ini.find_by_folder_name(folder.path.name)
            if entry is not None:
                ini.set_enabled(entry, enabled)
        ini.save()

        self._populating = True
        try:
            for item, folder in pairs:
                item.setCheckState(0, Qt.Checked if enabled else Qt.Unchecked)
                folder.ini_entry.enabled = enabled
        finally:
            self._populating = False

        self.statusBar().showMessage(
            f"{'Enabled' if enabled else 'Disabled'} {len(pairs)} folder(s) in {description}.", 5000
        )


def _kind_heading(kind: str) -> str:
    return {"OBJECT": "Objects", "POLYGON": "Polygons", "NETWORK": "Networks"}.get(kind, kind)


def _short_type_name(resource_path: str) -> str:
    name = resource_path.rsplit("/", 1)[-1]
    if name.endswith((".obj", ".fac", ".net")):
        name = name.rsplit(".", 1)[0]
    return name


def _toggled_type_count(old: TypeCount, enabled: bool) -> TypeCount:
    """A type's total instance count within one folder never changes from
    toggling it (disabling just moves instances from the live tile into the
    .xwcdisabled sidecar, and back) - so the new active/disabled split can be
    computed directly from the old total, without waiting for a rescan."""
    total = old.active_count + old.disabled_count
    return TypeCount(old.type_name, old.kind, total if enabled else 0, 0 if enabled else total)

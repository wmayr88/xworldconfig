"""Main application window: an edition/region/category/object-type tree built
from xworldconfig.scenery.discovery and lazily populated per-category from
xworldconfig.dsf.inventory. Whole-folder enable/disable is wired to
scenery_packs.ini via ini_parser; per-object-type toggling is not (that
needs xworldconfig.dsf.apply, not yet built), so type rows are read-only
counts for now."""
from pathlib import Path

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
from xworldconfig.dsf.inventory import ScanResult, scan_folder, scan_folders
from xworldconfig.ini_parser import SceneryPacksIni
from xworldconfig.scenery.discovery import SceneryFolder, discover

_ROLE_KIND = Qt.UserRole
_ROLE_FOLDER = Qt.UserRole + 1

_EDITION_LABELS = {"freeware": "Freeware (simHeaven X-World)", "pro": "Pro (simHeaven X-World-Pro)"}
_WARNING_COLOR = QColor("#c47a1f")


class _ScanSignals(QObject):
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
            result = scan_folder(self._folder_path)
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
        signals.finished.connect(self._on_scan_finished)
        signals.failed.connect(self._on_scan_failed)
        self._active_scans.append(signals)  # keep alive until the queued callback fires
        self._thread_pool.start(_ScanTask(item, folder.path, signals))
        return True

    def _is_unscanned(self, category_item: QTreeWidgetItem) -> bool:
        if category_item.childCount() != 1:
            return False
        placeholder = category_item.child(0)
        return placeholder.data(0, _ROLE_KIND) == "placeholder" and placeholder.text(0) != "Scanning..."

    def _on_scan_finished(self, item: QTreeWidgetItem, result: ScanResult) -> None:
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
                name = _short_type_name(type_count.type_name)
                row = QTreeWidgetItem([name, f"{type_count.active_count:,} ({type_count.disabled_count:,})"])
                row.setData(0, _ROLE_KIND, "type")
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
        self.scan_button.setEnabled(False)
        self.scan_all_button.setEnabled(False)
        self.progress_label.setText(_render_progress_bar(0, 0))
        self.progress_label.setVisible(True)

        folder_paths = [f.path for f in folders]
        signals = _BulkScanSignals()
        signals.progress.connect(self._on_bulk_scan_progress)
        signals.finished.connect(self._on_bulk_scan_finished)
        signals.failed.connect(self._on_bulk_scan_failed)
        self._active_bulk_scans.append(signals)  # keep alive until the queued callback fires
        self._thread_pool.start(_BulkScanTask(folder_paths, signals))

    def _on_bulk_scan_progress(self, done: int, total: int) -> None:
        self.progress_label.setText(_render_progress_bar(done, total))

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
        self.scan_button.setEnabled(True)
        self.scan_all_button.setEnabled(not self._ini_blocked)
        self.tree.setEnabled(not self._ini_blocked)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._populating or column != 0:
            return
        if item.data(0, _ROLE_KIND) != "category":
            return
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

    def _on_tree_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        kind = item.data(0, _ROLE_KIND)
        if kind == "region":
            self._show_region_context_menu(item, pos)
        elif kind == "category":
            self._show_category_context_menu(item, pos)

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


_PROGRESS_BAR_WIDTH = 80


def _render_progress_bar(done: int, total: int) -> str:
    if total <= 0:
        return "[" + "░" * _PROGRESS_BAR_WIDTH + "] scanning..."
    fraction = min(1.0, done / total)
    filled = round(_PROGRESS_BAR_WIDTH * fraction)
    bar = "█" * filled + "░" * (_PROGRESS_BAR_WIDTH - filled)
    return f"[{bar}] {done:,} / {total:,} tiles ({fraction * 100:.0f}%)"

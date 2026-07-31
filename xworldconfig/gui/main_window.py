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
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from xworldconfig import config
from xworldconfig.dsf.inventory import ScanResult, scan_folder
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("xworldconfig")
        self.resize(1100, 750)

        self.settings = config.load()
        self._thread_pool = QThreadPool.globalInstance()
        self._populating = False
        self._active_scans: list[_ScanSignals] = []

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Count"])
        self.tree.setColumnWidth(0, 480)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemChanged.connect(self._on_item_changed)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.choose_button = QPushButton("Choose Custom Scenery Folder...")
        self.choose_button.clicked.connect(self._choose_scenery_folder)
        layout.addWidget(self.choose_button)

        self.folder_label = QLabel()
        layout.addWidget(self.folder_label)

        prefix_form = QFormLayout()
        prefix_form.setContentsMargins(0, 0, 0, 0)
        self.freeware_prefix_edit = QLineEdit(self.settings.freeware_prefix)
        self.freeware_prefix_edit.editingFinished.connect(self._on_prefix_changed)
        prefix_form.addRow("X-World folder prefix:", self.freeware_prefix_edit)
        self.pro_prefix_edit = QLineEdit(self.settings.pro_prefix)
        self.pro_prefix_edit.editingFinished.connect(self._on_prefix_changed)
        prefix_form.addRow("X-World Pro folder prefix:", self.pro_prefix_edit)
        layout.addLayout(prefix_form)

        self.scan_button = QPushButton("Scan")
        self.scan_button.clicked.connect(self._rescan)
        layout.addWidget(self.scan_button)

        layout.addWidget(self.tree, 1)
        self.setCentralWidget(central)

        self._update_folder_label()
        if self.settings.custom_scenery_dir and Path(self.settings.custom_scenery_dir).is_dir():
            self._rescan()
        else:
            self._choose_scenery_folder()

    def _update_folder_label(self) -> None:
        if self.settings.custom_scenery_dir:
            self.folder_label.setText(f"  Custom Scenery: {self.settings.custom_scenery_dir}  ")
        else:
            self.folder_label.setText("  (no folder selected)  ")

    def _choose_scenery_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose Custom Scenery folder")
        if not chosen:
            return
        self.settings.custom_scenery_dir = chosen
        config.save(self.settings)
        self._update_folder_label()
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
                "Launch X-Plane at least once so it can generate this file, then click Scan again.",
            )

        folders = discover(scenery_dir, self.settings.freeware_prefix, self.settings.pro_prefix, ini)
        self._populate_tree(folders, ini_missing)

    def _populate_tree(self, folders: list[SceneryFolder], ini_missing: bool) -> None:
        self._populating = True
        try:
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

            if missing_count:
                self.tree.setEnabled(False)
                self.statusBar().showMessage(
                    f"Blocked: {missing_count} folder(s) not yet in scenery_packs.ini. "
                    "Launch X-Plane, then click Scan again.",
                )
                if not ini_missing:
                    QMessageBox.warning(
                        self,
                        "Folders not registered in scenery_packs.ini",
                        f"{missing_count} simHeaven folder(s) were found on disk but are not yet "
                        "listed in scenery_packs.ini.\n\n"
                        "X-Plane adds new entries to this file on launch, so it likely hasn't been "
                        "started since these folders were installed.\n\n"
                        "Launch X-Plane at least once, then click Scan again. The scenery list "
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
        if item.childCount() != 1 or item.child(0).data(0, _ROLE_KIND) != "placeholder":
            return  # already scanned or already loaded
        if item.child(0).text(0) == "Scanning...":
            return  # scan already in flight

        folder: SceneryFolder = item.data(0, _ROLE_FOLDER)
        item.child(0).setText(0, "Scanning...")

        signals = _ScanSignals()
        signals.finished.connect(self._on_scan_finished)
        signals.failed.connect(self._on_scan_failed)
        self._active_scans.append(signals)  # keep alive until the queued callback fires
        self._thread_pool.start(_ScanTask(item, folder.path, signals))

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
                row = QTreeWidgetItem([name, f"{type_count.count:,}"])
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


def _kind_heading(kind: str) -> str:
    return {"OBJECT": "Objects", "POLYGON": "Polygons", "NETWORK": "Networks"}.get(kind, kind)


def _short_type_name(resource_path: str) -> str:
    name = resource_path.rsplit("/", 1)[-1]
    if name.endswith((".obj", ".fac", ".net")):
        name = name.rsplit(".", 1)[0]
    return name
